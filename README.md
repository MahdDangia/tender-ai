# TenderAI

TenderAI is a production-oriented FastAPI application for construction companies to upload tender PDFs, extract key metrics, and review bid deadlines and penalty clauses using OpenAI-powered analysis.

## Features

- Company registration and login
- Protected client and admin dashboards
- PDF upload parsing with PyMuPDF
- OpenAI structured extraction for tender analysis
- Secure cookie-based authenticated sessions
- Startup admin bootstrap for local environments
- Health endpoint for deployment checks

## Local setup

1. Create a Python virtual environment.
2. Install dependencies:
   pip install -r requirements.txt
3. Copy `.env.example` to `.env` and update values.
4. Start the app:
   uvicorn main:app --reload

## Required environment variables

- APP_ENV: `development` or `production`
- SECRET_KEY: secret used to sign session cookies
- DATABASE_URL: database connection string
- OPENAI_API_KEY: API key used for tender analysis
- DEFAULT_ADMIN_EMAIL: bootstrapped admin email
- DEFAULT_ADMIN_PASSWORD: bootstrapped admin password

## Production notes

- Use a strong `SECRET_KEY` and set `APP_ENV=production` for HTTPS cookies.
- Replace the default SQLite database with PostgreSQL for multi-user production workloads.
- Add proper reverse-proxy TLS termination and monitoring before exposing the app publicly.
- Use a real session store or JWT flow if you expect large-scale user growth.
