import csv
import json

# Input and output files
csv_file = "Train_50_intent_rca_query.csv"
json_file = "Train_50_intent_rca_query.jsonl"

# Values that should be removed
invalid_values = {"", "0", "/"}

seen = set()
written_count = 0

with open(csv_file, mode="r", encoding="utf-8-sig", newline="") as csvf:
    csv_reader = csv.DictReader(csvf)

    with open(json_file, mode="w", encoding="utf-8") as jsonf:

        for row in csv_reader:

            # Remove blank column names and fields having 0, /, or blank values
            cleaned_row = {
                key.strip(): value.strip()
                for key, value in row.items()
                if key
                and value is not None
                and value.strip() not in invalid_values
            }

            # Skip completely empty rows
            if not cleaned_row:
                continue

            # Create a stable representation for duplicate checking
            duplicate_key = json.dumps(
                cleaned_row,
                sort_keys=True,
                ensure_ascii=False
            )

            # Skip duplicate rows
            if duplicate_key in seen:
                continue

            seen.add(duplicate_key)

            # Write valid JSONL
            jsonf.write(
                json.dumps(cleaned_row, ensure_ascii=False) + "\n"
            )

            written_count += 1

print(f"Success: {written_count} unique records converted")
print(f"Output file: {json_file}")

