# HODARI - Hodari Integrated School Management System

Django-based school management system for Hodari Christian School.

## Features
- Admissions pipeline with workflow stages
- Student management
- Academic tracking (lesson plans, report cards, progression)
- Attendance with QR codes and PWA
- Finance (fees, invoicing, payroll)
- HR and staff management
- Discipline and welfare tracking
- Communications and notifications
- Task management
- Parent portal

## Tech Stack
- Python / Django 5.x
- PostgreSQL
- Redis
- Celery
- Django Channels (WebSockets)
- Gunicorn + Nginx

## Deployment
- **Test**: `hodari.elimcoregroup.com:8443`
- **Live**: `connect.hodari.ac.tz:8444`
- Deploy script: `python deployment/deploy.py --env test|live`
# hisms_backend
