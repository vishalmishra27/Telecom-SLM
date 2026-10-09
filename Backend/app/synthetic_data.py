"""
Synthetic Data Generator — Telecom Billing RCA Pipeline

Generates realistic CSV data for all 9 ingestion domains.
Each generated dataset has correlated customer/account IDs so cross-domain
relationships work after ingestion.
"""

from __future__ import annotations

import csv
import io
import random
import string
import zipfile
from datetime import datetime, timedelta


def _rand_date(start: datetime, end: datetime) -> str:
    delta = (end - start).total_seconds()
    return (start + timedelta(seconds=random.uniform(0, delta))).strftime("%Y-%m-%d")


def _rand_datetime(start: datetime, end: datetime) -> str:
    delta = (end - start).total_seconds()
    return (start + timedelta(seconds=random.uniform(0, delta))).strftime("%Y-%m-%dT%H:%M:%S")


def _rand_id(prefix: str, width: int = 4) -> str:
    return f"{prefix}-{''.join(random.choices(string.digits, k=width))}"


# ---------------------------------------------------------------------------
# Shared pools — correlated across domains
# ---------------------------------------------------------------------------

CUSTOMER_TYPES = ["enterprise", "sme", "residential", "government", "wholesale"]
SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEVERITY_WEIGHTS = [0.3, 0.35, 0.25, 0.1]
SERVICES = ["4G-LTE", "5G-NR", "FiberBB", "IPTV", "VoLTE", "SD-WAN", "IoT-M2M", "MPLS-VPN"]
SITES = [f"SITE-{i:03d}" for i in range(1, 51)]
KPI_NAMES = ["Latency_ms", "Packet_Loss_%", "Throughput_Mbps", "Jitter_ms", "SINR_dB", "RSRP_dBm", "Availability_%", "Call_Drop_Rate_%"]
LOG_SOURCES = ["OSS-FM", "PCRF", "OCS", "BSS-Mediation", "CRM-API", "VoLTE-SBC", "RADIUS", "CDR-Pipeline"]
LOG_LEVELS = ["INFO", "WARN", "ERROR", "CRITICAL"]
LOG_LEVEL_WEIGHTS = [0.2, 0.3, 0.35, 0.15]
CURRENCIES = ["USD", "EUR", "GBP"]
CHARGE_CATEGORIES = ["subscription", "usage", "overage", "roaming", "one-time", "tax"]
COMPLAINT_REASONS = ["billing_error", "service_outage", "slow_speed", "dropped_calls", "overcharge", "contract_dispute", "roaming_charge"]
COMPLAINT_STATUSES = ["OPEN", "IN_REVIEW", "RESOLVED", "CLOSED", "ESCALATED"]
DISRUPTION_REASONS = ["Fiber cut", "Power outage", "Hardware failure", "Software bug", "Capacity overload", "DDoS attack", "Maintenance error", "Weather damage"]
ADJUSTMENT_TYPES = ["SLA_CREDIT", "GOODWILL", "BILLING_CORRECTION", "PROMOTIONAL"]
PAYMENT_STATUSES = ["SUCCESS", "FAILED", "PENDING", "REVERSED"]
FAILURE_TYPES = ["card_declined", "insufficient_funds", "expired_card", "bank_timeout", "fraud_block"]
ISSUER_RESPONSES = ["05-Do not honor", "51-Insufficient funds", "54-Expired card", "91-Issuer unavailable", "59-Suspected fraud"]


def generate_synthetic_data(num_customers: int = 10, events_per_customer: int = 5) -> dict[str, str]:
    """Generate correlated synthetic CSVs for all 9 domains.

    Returns dict mapping domain name → CSV string content.
    """
    start_date = datetime(2024, 1, 1)
    end_date = datetime(2025, 6, 30)

    # Build shared customer/account pools
    customers = []
    for i in range(1, num_customers + 1):
        cid = f"CUST-{i:04d}"
        aid = f"ACC-{i:05d}"
        customers.append({
            "customer_id": cid,
            "account_id": aid,
            "customer_type": random.choice(CUSTOMER_TYPES),
        })

    # Track IDs for cross-domain linking
    invoices_by_customer: dict[str, list[dict]] = {}
    incidents_by_customer: dict[str, list[str]] = {}
    charges_by_customer: dict[str, list[str]] = {}

    # --- 1. Network Alarms ---
    network_rows = []
    for c in customers:
        n = random.randint(max(1, events_per_customer - 2), events_per_customer + 3)
        for _ in range(n):
            sev = random.choices(SEVERITIES, SEVERITY_WEIGHTS)[0]
            network_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "failure_id": _rand_id("ALM", 5),
                "failure_type": random.choice(["cell_outage", "fiber_cut", "power_failure", "hw_degradation", "interference", "backhaul_loss"]),
                "severity": sev,
                "affected_service": random.choice(SERVICES),
                "affected_site": random.choice(SITES),
                "event_date": _rand_date(start_date, end_date),
                "duration_minutes": random.randint(5, 720),
                "impact_description": f"Service degradation on {random.choice(SERVICES)} — severity {sev}",
            })

    # --- 2. Billing ---
    billing_rows = []
    for c in customers:
        n = random.randint(max(1, events_per_customer - 1), events_per_customer + 2)
        inv_list = []
        chg_list = []
        for _ in range(n):
            inv_id = _rand_id("INV", 5)
            chg_id = _rand_id("CHG", 5)
            amount = round(random.uniform(50, 5000), 2)
            billing_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "invoice_id": inv_id,
                "charge_id": chg_id,
                "billing_period": random.choice(["2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4", "2025-Q1", "2025-Q2"]),
                "invoice_amount": amount,
                "currency": random.choice(CURRENCIES),
                "invoice_status": random.choice(["ISSUED", "PAID", "PAST_DUE", "DISPUTED"]),
                "due_date": _rand_date(start_date, end_date),
                "source_system": "BSS-Amdocs",
                "charge_category": random.choice(CHARGE_CATEGORIES),
                "charge_description": f"{random.choice(CHARGE_CATEGORIES).title()} charge for {random.choice(SERVICES)}",
                "charge_amount": round(amount * random.uniform(0.3, 1.0), 2),
            })
            inv_list.append({"invoice_id": inv_id, "amount": amount})
            chg_list.append(chg_id)
        invoices_by_customer[c["customer_id"]] = inv_list
        charges_by_customer[c["customer_id"]] = chg_list

    # --- 3. Complaints ---
    complaint_rows = []
    for c in customers:
        n = random.randint(0, max(1, events_per_customer - 2))
        chgs = charges_by_customer.get(c["customer_id"], [])
        for _ in range(n):
            complaint_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "dispute_id": _rand_id("COMP", 5),
                "charge_id": random.choice(chgs) if chgs else _rand_id("CHG", 5),
                "reason": random.choice(COMPLAINT_REASONS),
                "status": random.choice(COMPLAINT_STATUSES),
                "raised_date": _rand_date(start_date, end_date),
                "resolution_details": random.choice([
                    "Credit issued to account", "Charge reversed", "Under investigation",
                    "Customer contacted — resolved", "Escalated to billing team", ""
                ]),
            })

    # --- 4. Service Disruptions (Incidents) ---
    incident_rows = []
    for c in customers:
        n = random.randint(0, max(1, events_per_customer - 1))
        inc_ids = []
        for _ in range(n):
            did = _rand_id("INC", 5)
            ev_date = _rand_date(start_date, end_date)
            res_date = _rand_date(datetime.strptime(ev_date, "%Y-%m-%d"), end_date)
            sev = random.choices(SEVERITIES, SEVERITY_WEIGHTS)[0]
            incident_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "disruption_id": did,
                "service_type": random.choice(SERVICES),
                "disruption_reason": random.choice(DISRUPTION_REASONS),
                "severity": sev,
                "event_date": ev_date,
                "resolution_date": res_date,
                "impact_on_charges": f"Potential credit of ${random.randint(10, 500)} for {sev} outage",
            })
            inc_ids.append(did)
        incidents_by_customer[c["customer_id"]] = inc_ids

    # --- 5. PM Counters ---
    pm_rows = []
    for c in customers:
        n = random.randint(max(1, events_per_customer - 2), events_per_customer + 3)
        for _ in range(n):
            kpi = random.choice(KPI_NAMES)
            threshold = round(random.uniform(10, 100), 1)
            value = round(threshold * random.uniform(0.5, 2.0), 2)
            pm_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "counter_id": _rand_id("PMC", 5),
                "site_id": random.choice(SITES),
                "kpi_name": kpi,
                "value": value,
                "unit": kpi.split("_")[-1] if "_" in kpi else "units",
                "threshold": threshold,
                "severity": "HIGH" if value > threshold * 1.5 else ("MEDIUM" if value > threshold else "LOW"),
                "observed_at": _rand_datetime(start_date, end_date),
                "impact_description": f"{kpi} {'breached' if value > threshold else 'normal'} at {value}",
            })

    # --- 6. KPI / API ---
    api_rows = []
    for c in customers:
        n = random.randint(max(1, events_per_customer - 2), events_per_customer + 2)
        for _ in range(n):
            kpi = random.choice(KPI_NAMES)
            threshold = round(random.uniform(10, 100), 1)
            value = round(threshold * random.uniform(0.7, 1.8), 2)
            sev = random.choices(SEVERITIES, SEVERITY_WEIGHTS)[0]
            api_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "kpi_observation_id": _rand_id("KPI", 5),
                "kpi_name": kpi,
                "kpi_value": value,
                "threshold_value": threshold,
                "unit": kpi.split("_")[-1] if "_" in kpi else "units",
                "severity": sev,
                "measurement_window": random.choice(["5min", "15min", "1hour", "24hour"]),
                "observed_at": _rand_datetime(start_date, end_date),
                "source_system": random.choice(["PM-Collector", "NMS-OSS", "PCMD-Probe"]),
                "impact_description": f"{kpi} observed at {value} (threshold {threshold})",
            })

    # --- 7. System Logs ---
    log_rows = []
    for c in customers:
        n = random.randint(max(1, events_per_customer - 1), events_per_customer + 4)
        for _ in range(n):
            level = random.choices(LOG_LEVELS, LOG_LEVEL_WEIGHTS)[0]
            log_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "log_id": _rand_id("LOG", 5),
                "log_source": random.choice(LOG_SOURCES),
                "service_name": random.choice(SERVICES),
                "log_level": level,
                "message": random.choice([
                    "Connection timeout to upstream service",
                    "Rate limit exceeded for API gateway",
                    "Database query latency > 5000ms",
                    "Certificate expiration warning",
                    "Disk usage exceeds 90% threshold",
                    "Memory allocation failure in worker pool",
                    "Authentication failure for service account",
                    "Queue depth exceeded maximum capacity",
                ]),
                "trace_id": f"trace-{''.join(random.choices(string.hexdigits.lower(), k=16))}",
                "host_or_pod": f"{'pod' if random.random() > 0.5 else 'host'}-{random.randint(1, 99):02d}",
                "event_time": _rand_datetime(start_date, end_date),
                "source_system": random.choice(LOG_SOURCES),
                "impact_description": f"{level} event from {random.choice(LOG_SOURCES)}",
            })

    # --- 8. SLA Credits ---
    sla_rows = []
    for c in customers:
        incs = incidents_by_customer.get(c["customer_id"], [])
        n = min(len(incs), random.randint(0, max(1, events_per_customer - 3)))
        for j in range(n):
            sla_rows.append({
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "adjustment_id": _rand_id("ADJ", 5),
                "adjustment_type": random.choice(ADJUSTMENT_TYPES),
                "amount": round(random.uniform(10, 1000), 2),
                "currency": random.choice(CURRENCIES),
                "reason": f"SLA credit for incident {incs[j]}",
                "related_incident_id": incs[j],
                "issued_date": _rand_date(start_date, end_date),
                "status": random.choice(["APPLIED", "PENDING", "REVERSED"]),
            })

    # --- 9. Payment Failures ---
    payment_rows = []
    for c in customers:
        invs = invoices_by_customer.get(c["customer_id"], [])
        n = random.randint(max(1, events_per_customer - 2), events_per_customer + 1)
        for j in range(n):
            inv = invs[j % len(invs)] if invs else {"invoice_id": _rand_id("INV", 5), "amount": 100}
            status = random.choices(PAYMENT_STATUSES, [0.5, 0.3, 0.1, 0.1])[0]
            pay_date = _rand_date(start_date, end_date)
            row = {
                "customer_id": c["customer_id"],
                "account_id": c["account_id"],
                "payment_id": _rand_id("PAY", 5),
                "invoice_id": inv["invoice_id"],
                "payment_amount": round(inv["amount"] * random.uniform(0.8, 1.0), 2),
                "payment_date": pay_date,
                "payment_status": status,
                "card_last_four": f"{random.randint(1000, 9999)}",
                "issuer_response": random.choice(ISSUER_RESPONSES) if status == "FAILED" else "00-Approved",
                "payment_failure_id": _rand_id("DUN", 5) if status == "FAILED" else "",
                "failure_type": random.choice(FAILURE_TYPES) if status == "FAILED" else "",
                "failure_reason": random.choice(ISSUER_RESPONSES) if status == "FAILED" else "",
                "failure_date": pay_date if status == "FAILED" else "",
            }
            payment_rows.append(row)

    # Convert to CSV strings
    def rows_to_csv(rows: list[dict]) -> str:
        if not rows:
            return ""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()

    return {
        "network": rows_to_csv(network_rows),
        "billing": rows_to_csv(billing_rows),
        "complaints": rows_to_csv(complaint_rows),
        "incident": rows_to_csv(incident_rows),
        "pm_counters": rows_to_csv(pm_rows),
        "api": rows_to_csv(api_rows),
        "logs": rows_to_csv(log_rows),
        "sla_credits": rows_to_csv(sla_rows),
        "payment_failures": rows_to_csv(payment_rows),
    }


def generate_zip(num_customers: int = 10, events_per_customer: int = 5) -> bytes:
    """Generate a ZIP file containing all domain CSVs."""
    csvs = generate_synthetic_data(num_customers, events_per_customer)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for domain, content in csvs.items():
            zf.writestr(f"{domain}.csv", content)
    return buf.getvalue()
