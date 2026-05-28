import json
import os

state_file = "data/price_history.json"

if not os.path.exists(state_file):
    print("Hittade inte price_history.json!")
    exit(1)

with open(state_file, "r", encoding="utf-8") as f:
    state = json.load(f)

tui_g_map = {
    "G-000000292": "CHQ", "G-000000293": "CHQ", "G-000000294": "CHQ", "G-000000295": "CHQ",
    "G-000000243": "PMI", "G-000000238": "PMI", "G-000000562": "PMI",
    "G-000000653": "AYT", "G-000001539": "AYT"
}

migrated_count = 0

for run in state.get("history", []):
    for item in run.get("flights", []):
        if item.get("type") == "package" and item.get("source") == "tui":
            dest = item.get("destination")
            if dest in tui_g_map:
                item["destination"] = tui_g_map[dest]
                migrated_count += 1

print(f"Sanerade totalt {migrated_count} st TUI G-koder i price_history.json!")

with open(state_file, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print("Sparade price_history.json framgångsrikt!")
