# HISMS Role Permission Matrix (Phase 1 Draft)

## Roles

- Super Admin
- School Director
- Head Teacher
- Academic Officer
- Finance Officer
- HR Officer
- Teacher
- Class Teacher

## Domain-level access

- `students.read`: all roles except none
- `students.write`: Super Admin, School Director, Head Teacher, Academic Officer
- `academics.read`: all teaching/leadership roles
- `academics.write`: Super Admin, School Director, Head Teacher, Academic Officer
- `finance.read`: Super Admin, School Director, Finance Officer
- `finance.write`: Super Admin, Finance Officer
- `finance.reconcile_lock`: Super Admin, Finance Officer
- `hr.read`: Super Admin, School Director, HR Officer
- `hr.write`: Super Admin, HR Officer
- `hr.payroll_lock`: Super Admin, HR Officer
- `discipline.read`: leadership + class/subject teachers
- `discipline.write`: leadership + class teachers
- `audit.read`: Super Admin, School Director
- `audit.write`: system/service only (no manual UI write)

## Object-level restriction examples

- Teacher/Class Teacher:
  - read students in assigned classes only
  - write scores for assigned subjects/classes only
- Finance Officer:
  - cannot edit records in reconciled periods
- HR Officer:
  - cannot edit payroll after lock
- Academic Officer:
  - cannot alter assessments once term is locked
