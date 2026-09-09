-- Azure Cosmos DB for NoSQL time-range queries.
-- Run one query at a time in the Data Explorer query editor for chat-history-uat.
--
-- Window used below (inclusive):
--   Start: 2026-09-04 18:00:00 IST = 2026-09-04 12:30:00 UTC = 1788525000
--   End:   2026-09-09 18:00:00 IST = 2026-09-09 12:30:00 UTC = 1788957000
--
-- Cosmos _ts is the document's latest insert/update time in Unix seconds.
-- TimestampToDateTime expects milliseconds, so _ts is multiplied by 1000.

-- 1. CID summary from Friday evening through all currently available records.
SELECT
    message.data.cid AS cid,
    c.id AS document_id,
    c._ts AS timestamp,
    TimestampToDateTime(c._ts * 1000) AS updated_at_utc
FROM c
JOIN message IN c.messages
WHERE IS_DEFINED(message.data.cid)
    AND c._ts >= 1788525000
ORDER BY c._ts DESC;

-- 2. CID summary for the fixed start and end window.
SELECT
    message.data.cid AS cid,
    c.id AS document_id,
    c._ts AS timestamp,
    TimestampToDateTime(c._ts * 1000) AS updated_at_utc
FROM c
JOIN message IN c.messages
WHERE IS_DEFINED(message.data.cid)
    AND c._ts >= 1788525000
    AND c._ts <= 1788957000
ORDER BY c._ts DESC;

-- 3. Complete Cosmos documents for the fixed window.
SELECT *
FROM c
WHERE c._ts >= 1788525000
    AND c._ts <= 1788957000
ORDER BY c._ts DESC;

-- 4. Number of Cosmos documents in the fixed window.
SELECT VALUE COUNT(1)
FROM c
WHERE c._ts >= 1788525000
    AND c._ts <= 1788957000;
