from datetime import date


def generate_recommendation(status, patterns, pct):
    if status == '🟢':
        return "El estudiante mantiene un desempeño y asistencia regular. Se recomienda monitoreo de rutina."

    rec = []
    if pct < 70:
        rec.append("Notificar a Subdirección/Orientación por nivel crítico de ausentismo.")

    for p in patterns:
        if "Matemática" in p or "Inglés" in p:
            rec.append(
                "Coordinar entrevista individual para verificar dificultades de adaptación específicas en la asignatura.")
        elif "tardanzas" in p:
            rec.append("Verificar inconvenientes con la ruta de transporte o situaciones al inicio de la jornada.")

    if not rec:
        rec.append("Entrevistar al estudiante para evaluar factores socioemocionales o de salud.")

    return " ".join(rec)


def generate_pedagogical_act(student, patterns, recommendation):
    act_text = f"""
    ==================================================
              ACTA DE SEGUIMIENTO PEDAGÓGICO
    ==================================================
    Fecha: {date.today()}
    Estudiante: {student.name}
    Sección: {student.section}

    1. DIAGNÓSTICO Y PATRONES DETECTADOS:
    {chr(10).join(['- ' + p for p in patterns]) if patterns else '- Sin patrones críticos'}

    2. RECOMENDACIÓN DEL SISTEMA (IA/INDICADORES):
    {recommendation}

    3. PARTICIPANTES EN LA REUNIÓN:
    [ ] Docente Guía: __________________________
    [ ] Responsable / Padre de Familia: __________________________
    [ ] Estudiante: __________________________

    4. ACUERDOS Y COMPROMISOS:
    __________________________________________________
    __________________________________________________

    5. FIRMAS:

    _______________________        _______________________
       Docente / Orientador          Padre / Encargado
    ==================================================
    """
    return act_text