import datetime
import hashlib
import hmac
import os
from functools import lru_cache

import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


class Settings:
    def __init__(self):
        self.secret_key = os.getenv("SECRET_KEY", "dev-change-me")
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./tender_ai.sqlite")
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.app_env = os.getenv("APP_ENV", "development")
        self.cookie_secure = self.app_env == "production"
        self.default_admin_email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@tenderai.local")
        self.default_admin_password = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin123")


@lru_cache
def get_settings() -> Settings:
    return Settings()


SECRET_KEY = get_settings().secret_key

# --- DATABASE SETUP ---
DATABASE_URL = get_settings().database_url
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Database Table for Construction Companies
class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# Database Table for Tracked Tenders
class Tender(Base):
    __tablename__ = "tenders"
    id = Column(Integer, primary_key=True, index=True)
    project_name = Column(String)
    submission_deadline = Column(String)
    company_id = Column(Integer, ForeignKey("companies.id"))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# --- APP INITIALIZATION ---
app = FastAPI(
    title="TenderAI Platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "img-src 'self' data:; "
        "font-src 'self' https://cdn.tailwindcss.com"
    )
    return response


templates = Jinja2Templates(directory="templates")
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    settings = get_settings()
    db = SessionLocal()
    try:
        existing_admin = db.query(Company).filter(Company.email == settings.default_admin_email).first()
        if not existing_admin:
            admin = Company(
                company_name="TenderAI Admin",
                email=settings.default_admin_email,
                hashed_password=pwd_context.hash(settings.default_admin_password),
                is_admin=True,
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sign_company_cookie(company_id: int) -> str:
    digest = hmac.new(
        SECRET_KEY.encode("utf-8"),
        str(company_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{company_id}:{digest}"
z

def get_company_id_from_cookie(request: Request) -> int | None:
    raw_value = request.cookies.get("company_id")
    if not raw_value:
        return None

    try:
        company_id, signature = raw_value.rsplit(":", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        company_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        return int(company_id)
    except ValueError:
        return None


def get_current_company(request: Request, db: Session) -> Company | None:
    company_id = get_company_id_from_cookie(request)
    if company_id is None:
        return None

    return db.query(Company).filter(Company.id == company_id).first()


def get_openai_client() -> OpenAI:
    api_key = get_settings().openai_api_key
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OpenAI API key is not configured. Set OPENAI_API_KEY to enable tender analysis.",
        )
    return OpenAI(api_key=api_key)


# --- PANDAS / PYDANTIC SCHEMAS FOR AI ---
class TenderSummary(BaseModel):
    project_name: str = Field(description="Name of the construction project")
    submission_deadline: str = Field(description="Bid submission deadline")
    penalty_clauses: list[str] = Field(description="List of penalty clauses")
    key_materials: list[str] = Field(description="Required materials or scope")


# --- AUTHENTICATION & ROUTING ---
@app.get("/health")
async def health_check():
    return JSONResponse({"status": "ok", "service": "tender-ai"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"request": request})


@app.post("/login")
async def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    company = db.query(Company).filter(Company.email == email.lower().strip()).first()
    if not company or not pwd_context.verify(password, company.hashed_password):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    redirect_url = "/admin-panel" if company.is_admin else "/"
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key="company_id",
        value=sign_company_cookie(company.id),
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
    )
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request})


@app.post("/register")
async def register(
    company_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    normalized_email = email.strip().lower()
    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters long",
        )

    existing = db.query(Company).filter(Company.email == normalized_email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_company = Company(
        company_name=company_name.strip(),
        email=normalized_email,
        hashed_password=pwd_context.hash(password),
        is_admin=False,
    )
    db.add(new_company)
    db.commit()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="company_id")
    return response


# --- ADMIN PANEL ROUTE ---
@app.get("/admin-panel", response_class=HTMLResponse)
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    current_user = get_current_company(request, db)
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    all_companies = db.query(Company).all()
    all_tenders = db.query(Tender).all()

    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "request": request,
            "companies": all_companies,
            "tenders": all_tenders,
            "admin_name": current_user.company_name,
        },
    )


# --- CLIENT DASHBOARD & PDF ANALYSIS ROUTE ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    current_company = get_current_company(request, db)
    if not current_company:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.post("/analyze", response_model=TenderSummary)
async def analyze_tender(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    current_company = get_current_company(request, db)
    if current_company is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Uploaded PDF is empty")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid or unreadable PDF file") from exc

    try:
        full_text = "".join(page.get_text() for page in doc)
    finally:
        doc.close()

    try:
        client = get_openai_client()
        completion = client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a senior construction estimator analyzing a tender document. Extract exact project metrics.",
                },
                {
                    "role": "user",
                    "content": f"Analyze this construction tender document: {full_text[:25000]}",
                },
            ],
            response_format=TenderSummary,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="AI analysis failed. Please try again with a valid tender PDF.",
        ) from exc

    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise HTTPException(status_code=502, detail="AI returned no structured analysis")

    analysis_result = parsed.model_dump()

    new_tender = Tender(
        project_name=analysis_result["project_name"],
        submission_deadline=analysis_result["submission_deadline"],
        company_id=current_company.id,
    )
    db.add(new_tender)
    db.commit()
    return analysis_result