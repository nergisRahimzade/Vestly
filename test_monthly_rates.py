import tools

a = tools.call_tool("generate_journal_entries", {"entity_id": "DE", "period": "2026-09"})
b = tools.call_tool("generate_journal_entries", {"entity_id": "DE", "period": "2026-09"})

if a["status"] != "ok":
    print("FAILED:", a["error"])
    raise SystemExit

print(a["data"]["fingerprint"] == b["data"]["fingerprint"])   # want True
print(a["warnings"])