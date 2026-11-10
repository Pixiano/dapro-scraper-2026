"""Company competitor mechanism — live sources only, no LLM. See PLAN-COMPANY.md."""

from ..config import settings

UA = f"ScraperCompanyCLI/0.1 (+{settings.contact_url or 'contact not set'})"
