"""Seed the kb_demo.documents collection with 20 synthetic enterprise policies.

Each upsert goes through src.docs.upsert_document, which embeds via voyage-3
on every write — exactly the same path the editor UI uses, so the seeded
corpus and any later edits share one code path.

Run from the project root (ai-kb/):
    python -m data.generate
"""
import json
import os
import sys

from anthropic import Anthropic
from dotenv import load_dotenv

from src.db import get_db
from src.docs import upsert_document

load_dotenv()
anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

GEN_MODEL = "claude-haiku-4-5-20251001"

# (id, category, topic, key_facts)
DOCUMENT_SPECS = [
    ("hr-001", "HR", "Parental Leave Policy",
     "12 weeks paid leave; applies after 6 months tenure; both parents eligible"),
    ("hr-002", "HR", "Annual Leave Entitlement",
     "20 working days per year; pro-rated for part-time; carry-over up to 5 days"),
    ("hr-003", "HR", "Sick Leave Policy",
     "10 paid sick days per year; medical certificate required after 3 consecutive days"),
    ("hr-004", "HR", "Remote Work Policy",
     "Up to 3 days remote per week; team lead approval required; quarterly review"),
    ("hr-005", "HR", "Performance Review Cycle",
     "Annual cycle in March; 360-degree feedback; calibration meetings in April"),
    ("it-001", "IT", "VPN Setup Guide",
     "Cisco AnyConnect; download from internal portal; MFA via Authy"),
    ("it-002", "IT", "Password Policy",
     "Minimum 14 characters; rotation every 90 days; password manager required"),
    ("it-003", "IT", "Laptop Refresh Policy",
     "MacBook Pro every 3 years; option to keep old laptop for AUD 200"),
    ("it-004", "IT", "Software Procurement",
     "Submit ticket via ServiceNow; auto-approved under AUD 500; security review for SaaS"),
    ("it-005", "IT", "Incident Response Procedure",
     "Page on-call via PagerDuty; severity 1 within 15 minutes; postmortem within 5 days"),
    ("fin-001", "Finance", "Expense Reimbursement",
     "Submit via Concur within 30 days; receipts required over AUD 50; manager approval"),
    ("fin-002", "Finance", "Travel Booking Policy",
     "Book via Egencia; economy domestic; business class for flights over 6 hours"),
    ("fin-003", "Finance", "Corporate Card Usage",
     "AmEx Business; AUD 5000 monthly limit; personal use prohibited"),
    ("fin-004", "Finance", "Vendor Onboarding",
     "Submit W-9 equivalent; AP team approval; 30-day net payment terms"),
    ("prod-001", "Product", "Release Process",
     "Two-week sprints; release notes via Notion; staging soak for 24 hours"),
    ("prod-002", "Product", "Feature Flag Policy",
     "All new features behind LaunchDarkly flags; default off; gradual rollout"),
    ("prod-003", "Product", "Customer Escalation Path",
     "Tier 1 to Tier 2 within 4 hours; engineer escalation via Slack #escalations"),
    ("sec-001", "Security", "Data Classification",
     "Public, Internal, Confidential, Restricted; PII is Restricted by default"),
    ("sec-002", "Security", "Access Review Cadence",
     "Quarterly access review; auto-revoke after 90 days inactivity; SOC 2 audit annually"),
    ("sec-003", "Security", "Phishing Reporting",
     "Forward to phishing@company.com; do not click links; expect response within 2 hours"),
]


def generate_full_document(topic: str, key_facts: str) -> tuple[str, str]:
    """Use Claude to expand a topic + facts into a realistic 150-200-word policy document."""
    prompt = f"""Generate a realistic enterprise policy document.

Topic: {topic}
Key facts to include: {key_facts}

Requirements:
- Title: clear and direct, matching the topic
- Body: 150-200 words, in the voice of a corporate HR/IT/Finance team
- Include the key facts naturally; do not bullet-point them
- End with a contact point or escalation path
- Sound like a real internal document, not marketing copy

Return ONLY a JSON object with this shape (no preamble, no fences):
{{"title": "...", "body": "..."}}"""

    response = anthropic.messages.create(
        model=GEN_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    parsed = json.loads(text)
    return parsed["title"], parsed["body"]


def main():
    db = get_db()
    db.documents.delete_many({})
    print(f"Cleared kb_demo.documents. Generating {len(DOCUMENT_SPECS)} documents...\n")

    failures = 0
    for doc_id, category, topic, key_facts in DOCUMENT_SPECS:
        print(f"  [{doc_id}] {topic} ({category})...", end=" ", flush=True)
        try:
            title, body = generate_full_document(topic, key_facts)
        except Exception as e:
            print(f"Claude failed: {e}; using fallback.")
            failures += 1
            title = topic
            body = (
                f"Policy for {topic}. Key details: {key_facts}. "
                f"Contact the {category} team for questions."
            )
        try:
            upsert_document(doc_id, title, body, category)
            print(f"saved ({len(body)} chars)")
        except Exception as e:
            print(f"upsert failed: {e}")
            failures += 1

    final_count = db.documents.count_documents({})
    print(f"\nDone. {final_count} documents in kb_demo.documents.")
    if failures:
        print(f"WARN: {failures} step(s) hit a fallback or error.")
        sys.exit(2)


if __name__ == "__main__":
    main()
