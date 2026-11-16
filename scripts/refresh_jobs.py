#!/usr/bin/env python3
"""Public vacancy discovery feed for AE Live Search Desk.
API results are unverified leads, never validated employer offers.
No contact, applications, private user details, or CV data.
"""
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "jobs-feed.json"
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
BLOCKED = {
    "edison smart", "dualentry", "eventogy", "chaos", "trustpilot",
    "hozah", "sensor tower", "xapien", "goodfit", "closers club",
    "twilio", "klaviyo", "maxwell bond", "3search", "customer.io",
    "sona", "magentic", "ultimate asset",
}
TITLE_OK = re.compile(r"\b(account executive|strategic account manager|key account manager)\b", re.I)
TITLE_BAD = re.compile(r"\b(intern|graduate|junior|sales development|business development representative|head of sales|sales director|vice president|vp sales|part.time)\b", re.I)
UK = re.compile(r"\b(UK|United Kingdom|England|Scotland|Wales|Northern Ireland|Britain|London|Kent|Europe|EMEA|Worldwide|Global|Anywhere)\b", re.I)
GBP = re.compile(r"£\s*([\d,]+(?:\.\d+)?)\s*(k)?\b", re.I)

def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "AE-Search-Desk-Personal/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()

def day(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return str(s or "")[:10]

def recent(s):
    try:
        age = (TODAY - dt.date.fromisoformat(day(s))).days
        return 0 <= age <= 14
    except ValueError:
        return False

def gbp(s):
    found = []
    for digits, thousands in GBP.findall(str(s or "")):
        n = float(digits.replace(",", "")) * (1000 if thousands else 1)
        if 20000 <= n <= 500000:
            found.append(round(n))
    return (min(found), max(found)) if found else (None, None)

def indicative_score(title, description, pattern, posted):
    s = (title + " " + description).lower()
    score = 57 + (13 if "account executive" in title.lower() else 0)
    score += (4 if ("senior" in title.lower() or "mid market" in title.lower()) else 0)
    score += sum(2 for term in (
        "full cycle", "full-cycle", "new business", "prospect", "discovery",
        "negotiat", "consultative", "stakeholder", "roi", "account growth",
        "pipeline", "outbound"
    ) if term in s)
    score += (3 if pattern == "Remote" else 0) + (2 if recent(posted) else 0)
    return min(91, score)

def strengths(description):
    d = description.lower()
    mapping = [
        (("outbound", "prospect"), "Verified outbound and self-sourced pipeline experience"),
        (("full cycle", "full-cycle", "new business"), "Full-cycle B2B acquisition and closing experience"),
        (("negotiat", "commercial"), "Commercial negotiation and pricing experience"),
        (("roi", "business case"), "ROI business cases for senior stakeholders"),
        (("account growth", "upsell", "expand"), "Account expansion experience at SalesAPE.ai and ADI"),
        (("hubspot", "salesforce", "crm"), "HubSpot, Salesforce and Microsoft Dynamics 365 experience"),
    ]
    matches = [phrase for needles, phrase in mapping if any(needle in d for needle in needles)]
    return matches[:4] or ["Consultative B2B selling; full JD fit requires human review"]

def make(provider, source_id, company, title, place, pattern, industry, posted, url, description, employment, salary="", minimum=None, maximum=None):
    company, title, place = clean(company), clean(title), clean(place)
    if not company or not TITLE_OK.search(title) or TITLE_BAD.search(title):
        return None
    if company.casefold() in BLOCKED or not recent(posted):
        return None
    if not str(url).startswith("https://"):
        return None
    desc = clean(description)[:2000]
    gaps = [
        "Guaranteed GBP base salary not verified with employer",
        "Full job description and specialist requirements need human review",
    ]
    if pattern == "Unknown":
        gaps.append("Working pattern needs confirmation")
    if "confirm" in employment.lower() or employment == "Unknown":
        gaps.append("Permanent contract not confirmed")
    source_key = hashlib.sha1(f"{provider}:{source_id}".encode()).hexdigest()[:16]
    return {
        "id": "feed-" + source_key, "company": company, "role": title,
        "location": place, "pattern": pattern,
        "patternDetail": "Source-reported; confirm UK eligibility and working pattern",
        "industry": industry or "Other B2B",
        "salaryMin": minimum, "salaryMax": maximum,
        "salaryText": salary or "Not disclosed; verify base",
        "salaryVerified": False, "employment": employment,
        "datePosted": day(posted), "dateChecked": TODAY.isoformat(),
        "url": url, "source": provider,
        "sourceQuality": "Unverified API lead; advertised/estimated salary is not confirmed base",
        "fitScore": indicative_score(title, desc, pattern, posted),
        "strengths": strengths(desc), "gaps": gaps,
        "status": "Needs verification", "contact": "", "followUp": "",
        "notes": "Unverified discovery lead. No application authorised.",
        "isApplied": False, "isRejected": False,
        "reviewFlag": "Check employer URL, base, permanent contract, UK location",
        "shortlisted": False, "lastAction": ""
    }

def remotive():
    url = "https://remotive.com/api/remote-jobs?search=account%20executive"
    entries = []
    for item in get(url).get("jobs", []):
        loc = clean(item.get("candidate_required_location"))
        if not loc or not UK.search(loc):
            continue
        sal = str(item.get("salary") or "")
        mn, mx = gbp(sal)
        employment = "Full-time; permanent to confirm" if "full" in str(item.get("job_type", "")).lower() else "Unknown"
        job = make("Remotive", item.get("id") or item.get("url"),
                   item.get("company_name"), item.get("title"), "Remote — " + loc,
                   "Remote", item.get("category"), item.get("publication_date"),
                   item.get("url"), item.get("description"), employment,
                   sal, mn, mx)
        if job:
            entries.append(job)
    return entries

def adzuna(app_id, app_key):
    entries = []
    queries = [
        ("London", "account executive"), ("Kent", "account executive"),
        ("London", "enterprise account executive"), ("UK", "remote account executive")
    ]
    for where, what in queries:
        params = urllib.parse.urlencode({
            "app_id": app_id, "app_key": app_key, "results_per_page": 50,
            "what": what, "where": where, "permanent": 1, "full_time": 1,
            "max_days_old": 14, "sort_by": "date", "content-type": "application/json"
        })
        response = get("https://api.adzuna.com/v1/api/jobs/gb/search/1?" + params)
        for item in response.get("results", []):
            title, desc = clean(item.get("title")), clean(item.get("description"))
            where_label = clean((item.get("location") or {}).get("display_name") or where)
            combined = title + " " + desc
            if re.search(r"\b(fully remote|100% remote|work remotely|remote only)\b", combined, re.I):
                pattern = "Remote"
            elif re.search(r"\bhybrid\b", combined, re.I):
                pattern = "Hybrid"
            else:
                pattern = "Unknown"
            predicted = item.get("salary_is_predicted", True)
            sal_min, sal_max = item.get("salary_min"), item.get("salary_max")
            salary = "Adzuna predicted salary; NOT verified base" if predicted else "Salary from Adzuna; base NOT verified"
            if sal_min is not None:
                salary += f": £{float(sal_min):,.0f}–£{float(sal_max or sal_min):,.0f}"
            job = make("Adzuna", item.get("id") or item.get("redirect_url"),
                       (item.get("company") or {}).get("display_name"), title,
                       where_label, pattern, "B2B / industry to verify",
                       item.get("created"), item.get("redirect_url"), desc,
                       "Permanent (Adzuna classification)", salary,
                       round(float(sal_min)) if sal_min else None,
                       round(float(sal_max)) if sal_max else None)
            if job:
                entries.append(job)
    return entries

def main():
    jobs, sources, warnings = [], [], []
    # Preserve time-limited, individually researched employer leads across API refreshes.
    # The job status/dateChecked are not silently upgraded by an automated run.
    curated_path = ROOT / "data" / "researched-leads.json"
    if curated_path.exists():
        try:
            curated = json.loads(curated_path.read_text(encoding="utf-8"))
            reviewed = [
                j for j in curated.get("jobs", [])
                if isinstance(j, dict) and (
                    recent(j.get("datePosted")) or (
                        not j.get("datePosted") and recent(j.get("dateChecked"))
                    )
                )
            ]
            jobs.extend(reviewed)
            sources.append(f"Previously researched employer leads ({len(reviewed)})")
            if reviewed:
                warnings.append(
                    "Researched employer leads retain their original check dates; "
                    "they have not been revalidated by today's API refresh"
                )
        except (ValueError, OSError) as err:
            warnings.append(f"Researched-lead import failed: {str(err)[:120]}")
    try:
        found = remotive()
        jobs.extend(found)
        sources.append(f"Remotive ({len(found)})")
    except Exception as err:
        warnings.append(f"Remotive failed: {str(err)[:160]}")
    if os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"):
        try:
            found = adzuna(os.environ["ADZUNA_APP_ID"], os.environ["ADZUNA_APP_KEY"])
            jobs.extend(found)
            sources.append(f"Adzuna ({len(found)})")
        except Exception as err:
            warnings.append(f"Adzuna failed: {str(err)[:160]}")
    else:
        warnings.append("Adzuna not configured: add GitHub Actions secrets for wider UK coverage")
    if not sources:
        print("No providers available: " + "; ".join(warnings), file=sys.stderr)
        return 1
    dedup = {}
    for job in jobs:
        key = re.sub(r"[^a-z0-9]+", "", (job["company"] + job["role"] + job["location"]).lower())
        dedup.setdefault(key, job)
    unique = sorted(dedup.values(), key=lambda j: (j["datePosted"], j["fitScore"]), reverse=True)
    payload = {
        "schemaVersion": 1, "generatedAt": NOW.isoformat(timespec="seconds"),
        "providers": sources, "warnings": warnings, "freshnessDays": 14,
        "purpose": "Unverified leads; OTE and salary estimates never equal verified base",
        "jobs": unique[:250]
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved {len(payload['jobs'])} unverified vacancy leads")
    return 0

if __name__ == "__main__":
    sys.exit(main())
