import csv
import json

csv_file = "Train_50_intent_rca_query.csv"
json_file = "Train_50_intent_rca_query.jsonl"

invalid_values = {"", "0", "/"}

seen = set()
count = 0
skipped = 0

with open(csv_file, "r", encoding="utf-8-sig", newline="") as csvf:
    reader = csv.DictReader(csvf)

    print("Columns found:")
    print(reader.fieldnames)

    with open(json_file, "w", encoding="utf-8") as jsonf:

        for row in reader:

            cleaned = {
                str(k).strip(): str(v).strip()
                for k, v in row.items()
                if k is not None
                and v is not None
                and str(v).strip() not in invalid_values
            }

            if not cleaned:
                skipped += 1
                continue

            #
            # TEMPORARILY use the complete row
            # so records are not becoming 0
            #
            user_content = json.dumps(
                cleaned,
                ensure_ascii=False
            )

            record = {
                "messages": [
                    {
                        "role": "system",
                        "content": "Analyze the provided telecom record and identify the appropriate intent."
                    },
                    {
                        "role": "user",
                        "content": user_content
                    },
                    {
                        "role": "assistant",
                        "content": "rca"
                    }
                ]
            }

            duplicate_key = json.dumps(
                record,
                sort_keys=True,
                ensure_ascii=False
            )

            if duplicate_key in seen:
                skipped += 1
                continue

            seen.add(duplicate_key)

            jsonf.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

            count += 1

print()
print(f"Created : {count} fine-tuning records")
print(f"Skipped : {skipped}")
print(f"Output  : {json_file}")
