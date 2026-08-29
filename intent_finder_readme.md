# Intent finder

This script reads NORA context-history records and assigns an intent to each user request. It uses a small set of text rules, not a trained model. The main reason for starting with rules is traceability: for any row in the output, we can see the original message, the text we selected, and the rule that produced the result.

The current intent list is:

- `ticket` - create, update, close, or continue a ticket/case workflow
- `modify` - change or configure a customer service or device
- `rca` - investigate or troubleshoot a technical problem
- `query` - look up customer-specific information
- `general` - answer a general question that is not customer-specific
- `clarification_needed` - there is not enough information to choose one of the above

## Where the user text comes from

In the source data, a typical message looks like this:

```json
{
  "type": "user",
  "data": {
    "content": "router is showing no internet connection",
    "cid": "conversation-id"
  }
}
```

The script looks through `messages` for entries where `type` is `user`, `customer`, or `human`. It uses the latest matching message and reads the text from `data.content`. The CID comes from `data.cid`.

Some records do not contain a usable message. In that case, the script checks `user_inputs` for a request or issue summary. It also checks `user_inputs` for customer and device identifiers such as BAN, IMEI, MSISDN, account number, subscriber ID, and impacted device.

The extraction order is:

1. `messages[].data.content`
2. a useful text field inside `user_inputs`
3. a direct message field on the record
4. `not_found`

The selected source is written to `extracted.user_text.source`, so we do not have to guess which fallback was used.

## How classification works

Rules are checked in this order:

```text
ticket -> modify -> rca -> query -> general -> clarification_needed
```

The first match wins. The order matters. For example, "create a ticket because the router is offline" matches both ticket and RCA language, but the result is `ticket`.

### Ticket

The ticket rule looks for a ticket/case/incident identifier, an action such as create or close, or an existing ticket workflow for the same CID.

Examples:

```text
Create a support ticket
Check INC123456
Escalate this case
Close the existing ticket
```

Output rules:

```text
ticket.active_workflow
ticket.explicit_reference_or_action
```

### Modify

The modify rule requires both an action and something that can be changed. Actions include change, update, configure, enable, disable, reset, set up, activate, install, and provision. Targets include plan, APN, roaming, voicemail, service, device, HSI, feature, and line.

Examples:

```text
Set up HSI device
Enable international roaming
Reset voicemail
Change the customer plan
Configure the APN
```

Output rule: `modify.configuration_change`

### RCA

The RCA rule matches either an explicit diagnostic action such as investigate, diagnose, troubleshoot, or root cause, or a technical subject followed by a failure condition.

Technical subjects include router, internet, Wi-Fi, signal, call, roaming, device, service, and network. Failure conditions include not working, failed, offline, down, dropping, disconnected, no connection, and no internet connection.

Examples:

```text
Router is showing no internet connection
Signal keeps dropping
Troubleshoot the Apple Watch
Why is the service offline?
Investigate the network problem
```

For `router is showing no internet connection`, the match is:

```text
subject: router
failure: no internet connection
intent: rca
rule: rca.issue_diagnosis
```

This is a business assumption: a reported technical failure is treated as work that needs diagnosis. If the team wants to distinguish "reported issue" from "formal RCA request", we should add a separate intent instead of making the RCA rule more complicated.

### Query

The query rule is for a focused lookup or verification. It requires customer or device context plus question/lookup language such as what, when, show, check, find, verify, or confirm.

Examples:

```text
Check the customer's current plan
Is this device online?
Show the subscriber's usage
Customer wanted to know the hours of operation
Verify the account status
```

Output rule: `query.customer_question`

### General

The general rule is for information that is not tied to a particular customer or device. It looks for language such as what is, how does, explain, define, policy, procedure, or documentation.

Examples:

```text
What is the international roaming policy?
Explain how voicemail works
What is the procedure for device activation?
```

Output rule: `general.non_customer_question`

### Clarification needed

There are two clarification results:

- `clarification.missing_user_text` - an issue summary exists, but no user request could be extracted
- `clarification.no_rule_match` - user text exists, but none of the rules matched with enough confidence

Examples of text that may need review:

```text
Customer called again
Please help with this
Customer was transferred
```

## Reading the CSV

The column prefixes indicate where the values came from:

| Prefix | What it means |
|---|---|
| `source.*` | Original Cosmos data |
| `extracted.*` | A value selected or normalized by this script |
| `classification.*` | A decision or metadata added by this script |

The most useful review columns are:

| Column | Description |
|---|---|
| `source.messages` | Original message array |
| `extracted.user_text` | Text used for classification |
| `extracted.user_text[messages[].data.content]` | Text when it came from the normal message path |
| `extracted.user_text.source` | Exact extraction source or fallback |
| `extracted.issue` | Extracted issue summary |
| `extracted.has_customer_context` | Whether customer/device context was found |
| `classification.intent` | Assigned intent |
| `classification.rule` | Rule that matched |
| `classification.reason` | Short explanation |
| `classification.confidence` | Confidence configured for that rule |
| `classification.needs_human_review` | Review flag |
| `classification.version` | Rule-set version |

The confidence values are fixed values assigned to rules. They are not probabilities learned from historical data.

## Example audit

```text
source.messages:
  messages[].data.content = "router is showing no internet connection"

extracted:
  extracted.user_text = "router is showing no internet connection"
  extracted.user_text.source = "messages[].data.content"

classification:
  classification.intent = "rca"
  classification.rule = "rca.issue_diagnosis"
  classification.reason = "Customer issue investigation or diagnosis was requested."
  classification.confidence = 0.90
```

## How these rules were chosen

The starting point was the intent list in the original script. I then checked the actual JSON structure and a small sample of NORA messages to correct extraction and add obvious language variations. The rules are therefore data-informed heuristics. They were not trained on a labelled dataset and are not yet proof of business accuracy.

## Validation

Before enabling write-back, we should:

1. Run a sample of at least 100 records.
2. Add an `expected_intent` column and have someone from the business review it.
3. Compare `expected_intent` with `classification.intent`.
4. Review incorrect results by `classification.rule`.
5. Adjust the patterns and repeat with a larger sample.
6. Update `classification.version` whenever rule behavior changes.

Pay particular attention to technical issues that may not qualify as formal RCA requests, modification requests without a clear action word, customer questions with missing context, and ticket conversations that have already ended.

## Running it

The script reads `intent_app_config.env` from the same directory by default.

For the first run:

```env
INTENT_MAX_RECORDS=100
INTENT_WRITE_BACK=false
```

Then run:

```powershell
git switch codex/intent-classification-app
pip install -r requirements.txt
az login
python intent_app.py
```

Keep `INTENT_WRITE_BACK=false` until the reviewed sample is accurate enough for the intended use.
