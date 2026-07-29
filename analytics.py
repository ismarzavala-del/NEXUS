from datetime import timedelta, date
from database import Attendance, Student, Subject


def evaluate_student_risk(session, student_id):
    thirty_days_ago = date.today() - timedelta(days=30)
    records = session.query(Attendance).filter(
        Attendance.student_id == student_id,
        Attendance.date >= thirty_days_ago
    ).order_by(Attendance.date.asc()).all()

    if not records:
        return '🟢', 100.0, ["Sin registros suficientes"]

    total = len(records)
    presents = sum(1 for r in records if r.status in ['Presente', 'Tardanza'])
    pct = (presents / total) * 100 if total > 0 else 100.0

    patterns = []

    # 1. Ausencias por asignatura
    sub_absences = {}
    weekday_absences = {}
    consecutive_absences = 0
    max_consecutive = 0

    for r in records:
        if r.status == 'Ausente':
            # Consecutivas
            consecutive_absences += 1
            if consecutive_absences > max_consecutive:
                max_consecutive = consecutive_absences

            # Por asignatura
            sub_absences[r.subject_id] = sub_absences.get(r.subject_id, 0) + 1

            # Por día de la semana
            day_name = r.date.strftime('%A')
            day_translation = {'Monday': 'Lunes', 'Tuesday': 'Martes', 'Wednesday': 'Miércoles', 'Thursday': 'Jueves',
                               'Friday': 'Viernes'}
            es_day = day_translation.get(day_name, day_name)
            weekday_absences[es_day] = weekday_absences.get(es_day, 0) + 1
        else:
            consecutive_absences = 0

    # Evaluar patrones
    if max_consecutive >= 3:
        patterns.append(f"Acumula {max_consecutive} ausencias consecutivas")

    for sub_id, count in sub_absences.items():
        if count >= 3:
            sub = session.query(Subject).get(sub_id)
            patterns.append(f"Acumula {count} ausencias en {sub.name}")

    for day, count in weekday_absences.items():
        if count >= 3:
            patterns.append(f"Ausencias recurrentes los días {day} ({count} veces)")

    tardies = sum(1 for r in records if r.status == 'Tardanza')
    if tardies >= 3:
        patterns.append(f"Presenta {tardies} tardanzas en el último mes")

    # Clasificación
    if pct < 60 or len(patterns) >= 2 or max_consecutive >= 3:
        status = '🔴'
    elif pct < 85 or len(patterns) >= 1:
        status = '🟡'
    else:
        status = '🟢'

    return status, round(pct, 1), patterns


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