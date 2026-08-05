from datetime import timedelta, date
from database import Attendance, Student, Subject


# Etiquetas de patrones (tags), usadas por app.py para sugerir acuerdos/compromisos
# concretos en el Acta, sin tener que volver a parsear el texto humano del patrón.
TAG_TARDANZAS_CONSECUTIVAS = "tardanzas_consecutivas"
TAG_TENDENCIA_EMPEORANDO = "tendencia_empeorando"
TAG_TENDENCIA_MEJORANDO = "tendencia_mejorando"
TAG_PATRON_PUENTE = "patron_puente"
TAG_CASO_DISCIPLINARIO = "caso_disciplinario"
TAG_CASO_SALUD = "caso_salud"
TAG_AUSENCIAS_CONSECUTIVAS = "ausencias_consecutivas"

UMBRAL_RATIO_JUSTIFICACION = 3  # antes 8; ahora el sistema es más estricto


def evaluate_student_risk_detailed(session, student_id):
    """Versión completa: devuelve estado del semáforo, % de asistencia, lista de
    patrones en texto humano, y un set de 'tags' para que el resto del sistema
    (actas, alertas, casos) pueda reaccionar programáticamente sin volver a
    interpretar el texto."""
    thirty_days_ago = date.today() - timedelta(days=30)
    records = session.query(Attendance).filter(
        Attendance.student_id == student_id,
        Attendance.date >= thirty_days_ago
    ).order_by(Attendance.date.asc()).all()

    if not records:
        return {
            'status': '🟢', 'pct': 100.0,
            'patterns': ["Sin registros suficientes"], 'tags': set()
        }

    # Los días con "Permiso" (ausencia justificada) NO cuentan como riesgo:
    # se excluyen del cálculo de % de asistencia, igual que en la práctica real
    # una incapacidad médica o permiso oficial no debería penalizar al estudiante.
    registros_evaluables = [r for r in records if r.status != 'Permiso']
    permisos = [r for r in records if r.status == 'Permiso']

    total = len(registros_evaluables)
    presents = sum(1 for r in registros_evaluables if r.status in ['Presente', 'Tardanza'])
    pct = (presents / total) * 100 if total > 0 else 100.0

    patterns = []
    tags = set()

    # --- 1. Ausencias: consecutivas, por asignatura, por día de la semana ---
    sub_absences = {}
    weekday_absences = {}
    consecutive_absences = 0
    max_consecutive = 0

    day_translation = {'Monday': 'Lunes', 'Tuesday': 'Martes', 'Wednesday': 'Miércoles',
                       'Thursday': 'Jueves', 'Friday': 'Viernes'}

    for r in registros_evaluables:
        if r.status == 'Ausente':
            consecutive_absences += 1
            if consecutive_absences > max_consecutive:
                max_consecutive = consecutive_absences

            sub_absences[r.subject_id] = sub_absences.get(r.subject_id, 0) + 1

            day_name = r.date.strftime('%A')
            es_day = day_translation.get(day_name, day_name)
            weekday_absences[es_day] = weekday_absences.get(es_day, 0) + 1
        else:
            consecutive_absences = 0

    if max_consecutive >= 3:
        patterns.append(f"Acumula {max_consecutive} ausencias consecutivas")
        tags.add(TAG_AUSENCIAS_CONSECUTIVAS)

    for sub_id, count in sub_absences.items():
        if count >= 3:
            sub = session.query(Subject).get(sub_id)
            patterns.append(f"Acumula {count} ausencias en {sub.name if sub else 'una asignatura'}")

    for day, count in weekday_absences.items():
        if count >= 3:
            patterns.append(f"Ausencias recurrentes los días {day} ({count} veces)")

    # --- 2. Patrón "puente" (lunes / viernes) ---
    ausencias_lunes = weekday_absences.get('Lunes', 0)
    ausencias_viernes = weekday_absences.get('Viernes', 0)
    total_ausencias_dias = sum(weekday_absences.values())
    ausencias_puente = ausencias_lunes + ausencias_viernes
    if ausencias_puente >= 2 and total_ausencias_dias > 0 and (ausencias_puente / total_ausencias_dias) >= 0.6:
        patterns.append(
            f"Patrón 'puente': {ausencias_puente} de sus {total_ausencias_dias} ausencias son "
            f"lunes y/o viernes, sugiriendo extensión intencional del fin de semana")
        tags.add(TAG_PATRON_PUENTE)

    # --- 3. Racha de tardanzas consecutivas (no solo el total) ---
    tardies = sum(1 for r in registros_evaluables if r.status == 'Tardanza')
    consecutive_tardies = 0
    max_consecutive_tardies = 0
    for r in registros_evaluables:
        if r.status == 'Tardanza':
            consecutive_tardies += 1
            max_consecutive_tardies = max(max_consecutive_tardies, consecutive_tardies)
        else:
            consecutive_tardies = 0

    if tardies >= 3:
        patterns.append(f"Presenta {tardies} tardanzas en el último mes")
    if max_consecutive_tardies >= 3:
        patterns.append(f"Acumula {max_consecutive_tardies} tardanzas consecutivas seguidas")
        tags.add(TAG_TARDANZAS_CONSECUTIVAS)

    # --- 4. Ratio de justificación: Ausente (sin excusa) vs Permiso ---
    ausentes_sin_justificar = sum(1 for r in records if r.status == 'Ausente')
    permisos_justificados = len(permisos)

    if ausentes_sin_justificar >= UMBRAL_RATIO_JUSTIFICACION and permisos_justificados == 0:
        patterns.append(
            f"Caso disciplinario: {ausentes_sin_justificar} ausencias SIN ninguna justificación registrada")
        tags.add(TAG_CASO_DISCIPLINARIO)
    elif permisos_justificados >= UMBRAL_RATIO_JUSTIFICACION and ausentes_sin_justificar == 0:
        patterns.append(
            f"Caso de salud a monitorear: {permisos_justificados} ausencias, todas con justificación registrada")
        tags.add(TAG_CASO_SALUD)
    elif ausentes_sin_justificar >= UMBRAL_RATIO_JUSTIFICACION and permisos_justificados > 0:
        patterns.append(
            f"Mixto: {ausentes_sin_justificar} ausencias sin justificar y {permisos_justificados} justificadas "
            f"— revisar caso a caso")
        tags.add(TAG_CASO_DISCIPLINARIO)

    # --- 5. Tendencia: últimos 15 días vs los 15 anteriores ---
    quince_dias = date.today() - timedelta(days=15)
    recientes = [r for r in registros_evaluables if r.date >= quince_dias]
    anteriores = [r for r in registros_evaluables if r.date < quince_dias]

    def pct_de(lista):
        if not lista:
            return None
        pres = sum(1 for r in lista if r.status in ['Presente', 'Tardanza'])
        return (pres / len(lista)) * 100

    pct_reciente = pct_de(recientes)
    pct_anterior = pct_de(anteriores)

    if pct_reciente is not None and pct_anterior is not None:
        diferencia = pct_reciente - pct_anterior
        if diferencia <= -15:
            patterns.append(
                f"Tendencia EMPEORANDO: bajó de {pct_anterior:.0f}% a {pct_reciente:.0f}% de asistencia "
                f"en la segunda quincena")
            tags.add(TAG_TENDENCIA_EMPEORANDO)
        elif diferencia >= 15:
            patterns.append(
                f"Tendencia MEJORANDO: subió de {pct_anterior:.0f}% a {pct_reciente:.0f}% de asistencia "
                f"en la segunda quincena")
            tags.add(TAG_TENDENCIA_MEJORANDO)

    # --- Clasificación del semáforo ---
    if pct < 60 or len(patterns) >= 2 or max_consecutive >= 3 or TAG_CASO_DISCIPLINARIO in tags:
        status = '🔴'
    elif pct < 85 or len(patterns) >= 1:
        status = '🟡'
    else:
        status = '🟢'

    return {'status': status, 'pct': round(pct, 1), 'patterns': patterns, 'tags': tags}


def evaluate_student_risk(session, student_id):
    """Firma de compatibilidad con el resto del sistema (semáforo, acta en PDF, etc.)."""
    resultado = evaluate_student_risk_detailed(session, student_id)
    return resultado['status'], resultado['pct'], resultado['patterns']


def sugerir_acuerdos_compromisos(tags):
    """Sugiere hasta 2 acuerdos (lo que se compromete el estudiante) y 2 compromisos
    institucionales (lo que se compromete el docente/institución), según los patrones
    detectados. Si no hay patrones específicos, da sugerencias genéricas razonables."""
    acuerdos = []
    compromisos = []

    if TAG_TARDANZAS_CONSECUTIVAS in tags:
        acuerdos.append("El estudiante se compromete a llegar a más tardar a las 7:00 am todos los días.")
        compromisos.append("El docente orientador registrará y revisará la puntualidad semanalmente.")

    if TAG_PATRON_PUENTE in tags:
        acuerdos.append("El estudiante se compromete a no faltar los días lunes ni viernes sin justificación.")
        compromisos.append("El docente orientador dará seguimiento específico los días lunes y viernes.")

    if TAG_AUSENCIAS_CONSECUTIVAS in tags:
        acuerdos.append("El estudiante se compromete a no acumular ausencias consecutivas sin avisar.")
        compromisos.append("La institución contactará al responsable ante cualquier ausencia no justificada.")

    if TAG_CASO_DISCIPLINARIO in tags:
        acuerdos.append("El estudiante se compromete a justificar toda ausencia dentro de las 48 horas siguientes.")
        compromisos.append("El docente orientador citará al padre/madre/responsable en caso de reincidencia.")

    if TAG_CASO_SALUD in tags:
        acuerdos.append("El estudiante/familia se compromete a informar con anticipación cuando sea posible.")
        compromisos.append("La institución dará seguimiento cercano por posible situación de salud recurrente.")

    if TAG_TENDENCIA_EMPEORANDO in tags:
        acuerdos.append("El estudiante se compromete a asistir con normalidad durante las próximas 2 semanas.")
        compromisos.append("El docente orientador hará una revisión de seguimiento en 15 días.")

    # Rellenar con sugerencias genéricas si hay menos de 2 de cada tipo
    genericos_acuerdos = [
        "El estudiante se compromete a mantener una asistencia regular y puntual.",
        "El estudiante se compromete a comunicar oportunamente cualquier inasistencia."
    ]
    genericos_compromisos = [
        "El docente orientador dará seguimiento periódico al caso.",
        "La institución brindará el acompañamiento necesario según el reglamento interno."
    ]

    for g in genericos_acuerdos:
        if len(acuerdos) >= 2:
            break
        if g not in acuerdos:
            acuerdos.append(g)

    for g in genericos_compromisos:
        if len(compromisos) >= 2:
            break
        if g not in compromisos:
            compromisos.append(g)

    return acuerdos[:2], compromisos[:2]


def sugerir_normativa_aplicable(tags, ausentes_sin_justificar, permisos_justificados):
    """Traduce los patrones/tags detectados a los artículos aplicables del Manual de
    Convivencia y Reglamento Interno Escolar del INDET (2025), citando texto literal
    del reglamento y la sanción correspondiente según su Art. 25.

    Fuente: 'Manual de Convivencia y Reglamento Interno Escolar del INDET 2025'
    — Art. 20 (Deberes de los estudiantes, numerales 1-63), Art. 21 (Clasificación
    de infracciones: Leves 1-33, Graves 34-48, Muy Graves 49-63), Art. 25 (Sanciones).
    """
    citas = []

    if TAG_CASO_DISCIPLINARIO in tags or ausentes_sin_justificar >= UMBRAL_RATIO_JUSTIFICACION:
        citas.append({
            'nivel': 'GRAVE',
            'articulo': 'Art. 20, numeral 34 (Deberes del estudiante)',
            'texto': '"No ausentarse de manera frecuente y sin justificación de las clases '
                     'y las actividades programadas."',
            'clasificacion': 'Art. 21/23: infracción GRAVE (numerales 34-48).',
            'sancion_sugerida': 'Art. 25 — Infracciones Graves: informar y hacer conciencia a '
                                'padres de familia por escrito (docente orientador); posible '
                                'servicio comunitario, terapias psicológicas, acción de '
                                'reparación, o suspensión de 1 a 3 días con educación a distancia.'
        })
    elif ausentes_sin_justificar > 0:
        citas.append({
            'nivel': 'LEVE',
            'articulo': 'Art. 20, numeral 19 (Deberes del estudiante)',
            'texto': '"Asistir puntualmente a la institución y entrar a clases, respetando '
                     'los horarios establecidos... En caso de no hacerlo, traer la debida '
                     'justificación."',
            'clasificacion': 'Art. 21/22: infracción LEVE (numerales 1-33).',
            'sancion_sugerida': 'Art. 25 — Infracciones Leves: amonestación verbal; si es '
                                'reiterada, amonestación escrita e informar a padres de '
                                'familia por parte del docente orientador.'
        })

    if TAG_TARDANZAS_CONSECUTIVAS in tags:
        citas.append({
            'nivel': 'LEVE',
            'articulo': 'Art. 20, numeral 19 y Art. 5, numeral 5',
            'texto': '"Los estudiantes tienen la posibilidad de presentarse hasta 15 minutos '
                     'tarde... Si pasara de ese tiempo, deberá presentar una justificación '
                     'o un audio de parte de su representante."',
            'clasificacion': 'Art. 21/22: infracción LEVE.',
            'sancion_sugerida': 'Art. 25 — Infracciones Leves: amonestación verbal/escrita; '
                                'informar a padres de familia por parte del docente orientador.'
        })

    if permisos_justificados >= UMBRAL_RATIO_JUSTIFICACION and ausentes_sin_justificar == 0:
        citas.append({
            'nivel': 'SIN INFRACCIÓN — Seguimiento de salud',
            'articulo': 'Art. 4 (De las ausencias y llegadas tardías)',
            'texto': '"En caso de ausencias por... enfermedad será necesario presentar '
                     'certificado médico que así lo demuestre."',
            'clasificacion': 'No constituye infracción: las ausencias están debidamente '
                             'justificadas conforme al Art. 4.',
            'sancion_sugerida': 'No aplica sanción disciplinaria. Se recomienda dar '
                                'seguimiento cercano por posible situación de salud recurrente.'
        })

    return citas


def get_institutional_semaphore(session):
    students = session.query(Student).all()
    summary = {'🟢': [], '🟡': [], '🔴': []}

    for st in students:
        status, pct, patterns = evaluate_student_risk(session, st.id)
        summary[status].append({
            'student': st,
            'pct': pct,
            'patterns': patterns
        })
    return summary


def get_alertas_docente(session, seccion):
    """Alertas 'accionables' para el banner del docente orientador:
    1) Alumnos que faltaron HOY sin justificación.
    2) Alumnos en riesgo alto (🔴) que todavía no tienen un caso de seguimiento abierto."""
    from database import CaseTracker

    alertas = []
    estudiantes = session.query(Student).filter_by(section=seccion).all()

    hoy = date.today()
    for est in estudiantes:
        falta_hoy = session.query(Attendance).filter_by(
            student_id=est.id, date=hoy, status='Ausente'
        ).first()
        if falta_hoy:
            alertas.append({
                'tipo': 'ausencia_hoy',
                'estudiante': est,
                'detalle': f"{est.name} está marcado como AUSENTE hoy ({hoy.strftime('%d/%m/%Y')}) sin justificación."
            })

    for est in estudiantes:
        resultado = evaluate_student_risk_detailed(session, est.id)
        if resultado['status'] == '🔴':
            caso_abierto = session.query(CaseTracker).filter(
                CaseTracker.student_id == est.id,
                CaseTracker.status != 'Cerrado'
            ).first()
            if not caso_abierto:
                alertas.append({
                    'tipo': 'riesgo_alto_sin_caso',
                    'estudiante': est,
                    'detalle': f"{est.name} está en riesgo ALTO 🔴 ({resultado['pct']}% asistencia) y no tiene un caso de seguimiento abierto.",
                    'tags': resultado['tags']
                })

    return alertas