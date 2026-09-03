import csv
import json

csv_file = "Train_50_intent_rca_query.csv"
json_file = "Train_50_intent_rca_query.jsonl"

invalid_values = {"", "0", "/"}

seen = set()
count = 0

with open(csv_file, "r", encoding="utf-8-sig", newline="") as csvf:
    reader = csv.DictReader(csvf)

    with open(json_file, "w", encoding="utf-8") as jsonf:

        for row in reader:

            cleaned = {
                k.strip(): v.strip()
                for k, v in row.items()
                if k
                and v is not None
                and v.strip() not in invalid_values
            }

            if not cleaned:
                continue

            # CHANGE THESE COLUMN NAMES
            user_text = cleaned.get("source.issue", "")
            assistant_answer = cleaned.get("classification", "")

            if not user_text or not assistant_answer:
                continue

            record = {
                "messages": [
                    {
                        "role": "system",
                        "content": "Classify the customer issue into the correct intent."
                    },
                    {
                        "role": "user",
                        "content": user_text
                    },
                    {
                        "role": "assistant",
                        "content": assistant_answer
                    }
                ]
            }

            duplicate_key = json.dumps(record, sort_keys=True)

            if duplicate_key in seen:
                continue

            seen.add(duplicate_key)

            jsonf.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

            count += 1

print(f"Created {count} fine-tuning records")
