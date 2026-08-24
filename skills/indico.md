# Indico events in the OKG

Indico (https://indico.cern.ch) is CERN's event-management system. On this
deployment its content arrives through the `indico` connector
(`archi.sources.indico`), which projects configured categories/events into the
graph — there is no live Indico API tool on a v3 instance; answer from the
graph, and say so when the graph's snapshot is older than the question needs.

## What the connector puts in the graph

- `meeting_minutes` nodes per ingested event (title, date, category, canonical
  `https://indico.cern.ch/event/<event_id>/` URL in attrs).
- `document` nodes for event attachments (slides, PDFs) with
  `contains` edges from the meeting, and `document_chunk` nodes carrying the
  extracted text (`member_of` / `contains` chunk edges).
- Cross-references: chunks that mention JIRA keys, releases, or sites get
  `references` edges via the extraction/enrichment layer, so meetings link
  into the operational graph.

## How to answer Indico questions

- **Event by ID or pasted URL**: extract the integer event id from the URL
  path and `search` for it (event ids and Indico URLs are indexed); `trace`
  the `meeting_minutes` node to walk its documents and chunks.
- **"What was discussed about X"**: `search` for the topic, filter to
  `meeting_minutes` / `document_chunk` subtypes, then expand promising chunks
  to their parent meeting for date/context before synthesizing.
- **"Latest <recurring meeting>"**: search the meeting-series name, order hits
  by the meeting date attr, and check the pinned generation's freshness — if
  the newest meeting in the graph predates the series cadence, the snapshot is
  behind; report that explicitly instead of presenting the newest ingested
  meeting as current.
- **Attachment contents**: the chunks ARE the attachment text. Quote from
  chunks and cite the parent document + meeting URL.

## When NOT to lean on this data

- Live/administrative Indico actions (registering, uploading, permissions)
  are out of scope — point the user at the Indico UI.
- Events outside the configured categories are not in the graph; absence of a
  meeting is evidence of scope, not of the event's nonexistence. Name the
  configured scope when reporting a miss (the `indico` registry entry's
  params show which categories/events are ingested).
