# NORA intent finder

## Purpose

The intent finder classifies each NORA user request into one of these intents:

- `ticket`
- `modify`
- `rca`
- `query`
- `general`
- `clarification_needed`

It is a deterministic rules engine, not a trained machine-learning model. Every decision includes the extracted user text, matched rule, confidence, and explanation so the team can audit it.

## How the data is extracted

The program reads context-history records from Cosmos DB and uses the actual NORA message structure:

```text
messages[].type
messages[].data.content
messages[].data.cid
user_inputs
```

For every record, it:

1. Finds messages where `messages[].type` is `user`, `customer`, or `human`.
2. Selects the latest matching user message.
3. Extracts its text from `messages[].data.content`.
4. Extracts the conversation ID from `messages[].data.cid`.
5. Uses structured `user_inputs` as a fallback when message text is unavailable.
6. Reads customer and device identifiers from the source record and `user_inputs`.
7. Normalizes whitespace and evaluates the intent rules.

The program never intentionally selects an assistant response as the user request.

## How to identify CSV fields

| Column | Meaning |
|---|---|
| `source.*` | Original fields copied from the Cosmos JSON |
| `source_id` | Original Cosmos document ID |
| `conversation_id` | CID extracted from the nested message, or source ID as a fallback |
| `extracted.user_text` | Final user text selected by the program |
| `extracted.user_text[messages[].data.content]` | Selected user text when it came from this exact JSON path |
| `extracted.user_text.source` | Actual extraction path used |
| `extracted.issue` | Issue summary extracted from the source or `user_inputs` |
| `extracted.has_customer_context` | Whether a customer, account, subscriber, or impacted device was found |
| `classification.intent` | Intent assigned by the program |
| `classification.rule` | Stable identifier of the rule that matched |
| `classification.confidence` | Fixed confidence configured for that rule |
| `classification.reason` | Human-readable explanation |
| `classification.needs_human_review` | Whether the result should be reviewed |
| `classification.version` | Version of the rules used |
| `classification.classified_at` | UTC classification timestamp |

`INTENT_INCLUDE_SOURCE_FIELDS=true` must be configured to include the original `source.*` columns.

## Rule evaluation order

Rules are evaluated in this order:

```text
ticket -> modify -> rca -> query -> general -> clarification_needed
```

The first matching rule wins. For example, "Create a ticket because the router is offline" contains both ticket and RCA language, but it becomes `ticket` because ticket is evaluated first.

## 1. Ticket

Ticket applies when the user explicitly refers to a ticket, case, incident, or service request, or when the same CID is already in an active ticket workflow.

Simplified logic:

```python
has_ticket_reference = any(
    value in user_text
    for value in ["INC123", "case", "service request", "ticket"]
)

has_ticket_action = any(
    phrase in user_text
    for phrase in [
        "create ticket",
        "raise ticket",
        "open case",
        "submit incident",
        "update ticket",
        "close ticket",
        "cancel ticket",
        "reopen ticket",
        "escalate ticket",
    ]
)

if active_ticket_workflow:
    intent = "ticket"
elif has_ticket_reference or has_ticket_action:
    intent = "ticket"
```

| Rule | Meaning |
|---|---|
| `ticket.active_workflow` | A previous record with the same CID started a ticket workflow |
| `ticket.explicit_reference_or_action` | Explicit ticket/case reference or action |

Examples:

- "Create a support ticket"
- "Check INC123456"
- "Escalate this case"
- "Close the existing ticket"

## 2. Modify

Modify applies when the user requests a configuration, feature, device, or service change.

Simplified logic:

```python
has_modify_action = any(
    action in user_text
    for action in [
        "change", "modify", "update", "configure", "enable",
        "disable", "reset", "set up", "setup", "activate",
        "install", "provision",
    ]
)

has_modifiable_target = any(
    target in user_text
    for target in [
        "configuration", "setting", "feature", "plan", "apn",
        "service", "roaming", "voicemail", "data", "device",
        "internet", "hsi", "line",
    ]
)

if has_modify_action and has_modifiable_target:
    intent = "modify"
```

Rule: `modify.configuration_change`

Examples:

- "Set up HSI device"
- "Enable international roaming"
- "Reset voicemail"
- "Change the customer plan"
- "Configure the APN"

## 3. RCA

RCA applies when the user explicitly requests troubleshooting or describes a technical subject together with a failure condition.

Simplified logic:

```python
has_diagnostic_action = any(
    phrase in user_text
    for phrase in [
        "investigate", "diagnose", "troubleshoot", "troubleshooting",
        "root cause", "run rca", "perform rca",
    ]
)

has_technical_subject = any(
    subject in user_text
    for subject in [
        "router", "internet", "wifi", "signal", "call",
        "roaming", "device", "service", "network",
    ]
)

has_failure = any(
    failure in user_text
    for failure in [
        "not working", "failed", "failing", "offline", "down",
        "dropping", "disconnect", "issue", "problem",
        "no connection", "no internet connection",
    ]
)

if has_diagnostic_action:
    intent = "rca"
elif has_technical_subject and has_failure:
    intent = "rca"
```

Rule: `rca.issue_diagnosis`

Examples:

- "Router is showing no internet connection"
- "Signal keeps dropping"
- "Troubleshoot the Apple Watch"
- "Why is the service offline?"
- "Investigate the network problem"

Example audit trail:

```text
source:
messages[].data.content = "router is showing no internet connection"

extracted:
extracted.user_text = "router is showing no internet connection"

matching evidence:
technical subject = "router"
failure condition = "no internet connection"

decision:
classification.intent = "rca"
classification.rule = "rca.issue_diagnosis"
classification.confidence = 0.90
```

The RCA rule currently assumes that a reported technical failure requires diagnosis. If the business treats a reported issue and a formal RCA request as different intents, this must be split into separate categories.

## 4. Query

Query applies to a focused lookup or verification request when customer or device context exists.

Customer context includes values such as:

```text
customer ID, customer name, BAN, IMEI, MSISDN,
impacted device, account number, or subscriber ID
```

Simplified logic:

```python
has_query_language = (
    user_text.startswith(
        ("what", "when", "where", "which", "who", "how many",
         "is", "are", "does", "do", "did", "has", "have", "can")
    )
    or any(
        phrase in user_text
        for phrase in [
            "show", "tell", "provide", "check", "find", "get",
            "display", "verify", "confirm", "wanted to know",
            "would like to know",
        ]
    )
)

if has_customer_context and has_query_language:
    intent = "query"
```

Rule: `query.customer_question`

Examples:

- "Check the customer's current plan"
- "Is this device online?"
- "Show the subscriber's usage"
- "Customer wanted to know the hours of operation"
- "Verify the account status"

## 5. General

General applies to informational questions that are not tied to a particular customer, account, subscriber, or device.

Simplified logic:

```python
has_general_language = any(
    phrase in user_text
    for phrase in [
        "in general", "generally", "documentation", "policy",
        "procedure", "how does", "what is", "explain",
        "define", "meaning of",
    ]
)

if has_general_language and not has_customer_context:
    intent = "general"
```

Rule: `general.non_customer_question`

Examples:

- "What is the international roaming policy?"
- "Explain how voicemail works"
- "What is the procedure for device activation?"
- "Define high-speed internet"

## 6. Clarification needed because user text is missing

This applies when an issue summary exists but no user message could be extracted.

```python
if not extracted_user_text and extracted_issue:
    intent = "clarification_needed"
```

Rule: `clarification.missing_user_text`

The configured confidence is `0.72`, and human review is required.

## 7. Clarification needed because no rule matched

This is the final fallback when user text exists but no higher-confidence rule matched.

```python
if extracted_user_text and no_other_rule_matched:
    intent = "clarification_needed"
```

Rule: `clarification.no_rule_match`

Examples:

- "Customer called again"
- "Please help with this"
- "Need assistance"
- "Customer was transferred"

The configured confidence is `0.55`, and human review is required.

## How the rules were defined

The rules were created from:

1. The intent categories and initial patterns in the supplied intent application.
2. The actual Cosmos JSON structure observed in the exported records.
3. Representative user messages visible in the review sample.
4. Common telecom workflows such as troubleshooting, configuration changes, lookups, and ticket handling.

These are data-informed heuristics. They were not trained on a labelled dataset and are not yet statistical proof of business accuracy.

## How to validate the rules

Before enabling write-back:

1. Set `INTENT_MAX_RECORDS=100`.
2. Keep `INTENT_WRITE_BACK=false`.
3. Run `python intent_app.py`.
4. Have a business reviewer add an `expected_intent` column for the sample.
5. Compare `expected_intent` with `classification.intent`.
6. Review false positives and false negatives for each intent.
7. Update the rules and increment `classification.version`.
8. Repeat with a larger and more diverse sample.

The most useful review columns are:

```text
extracted.user_text
extracted.user_text.source
extracted.issue
extracted.has_customer_context
classification.intent
classification.rule
classification.reason
classification.confidence
classification.needs_human_review
```

## Running the program

The application automatically loads `intent_app_config.env` from the same directory.

```powershell
git switch codex/intent-classification-app
pip install -r requirements.txt
az login
python intent_app.py
```

For initial validation:

```env
INTENT_MAX_RECORDS=100
INTENT_WRITE_BACK=false
```

Do not enable `INTENT_WRITE_BACK=true` until the business-reviewed sample demonstrates acceptable accuracy.
