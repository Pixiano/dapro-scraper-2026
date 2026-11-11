"""Company competitor mechanism — live sources only, no LLM. See PLAN-COMPANY.md."""

from ..config import settings

# Wikimedia only: it requires a contact. Every other host gets the plain UA,
# so the contact address is not sent to arbitrary news/blog sites.
UA = f"ScraperCompanyCLI/0.1 (+{settings.contact_url or 'contact not set'})"
UA_PLAIN = "Mozilla/5.0 (compatible; ScraperCompanyCLI/0.1)"
