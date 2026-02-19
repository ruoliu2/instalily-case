# Instalily x PartSelect Chat Agent PRD (v1)

## 1) Objective
Build a chat agent for PartSelect focused on **Refrigerator** and **Dishwasher** parts that helps customers:
- identify compatible parts for a model,
- troubleshoot common symptoms,
- understand install complexity,
- make confident purchase decisions,
- get basic order and post-purchase support.

The agent must stay in-scope (parts + repair + purchase flow) and decline unrelated requests.

## 2) Problem Statement
Customers often know a symptom ("not draining", "ice maker not working") but not the exact part. They need fast, model-specific guidance without manually browsing many diagrams, Q&A pages, and part listings.

## 3) Target Users
- DIY homeowners repairing appliances.
- Property managers / small maintenance teams.
- Repeat customers who know part numbers but need compatibility confirmation.

## 4) In-Scope
- Refrigerator + Dishwasher only.
- Model lookup and compatibility checks.
- Part recommendations with rationale and confidence.
- Repair guidance grounded in available content (Q&A, symptoms, videos, instructions).
- Purchase-assist responses (availability, price shown on site, part alternatives if out of stock).
- Basic order-help intent routing (status, returns policy links, escalation to support).

## 5) Out-of-Scope (v1)
- Non-appliance categories.
- Real-time technician booking.
- Full autonomous checkout.
- Legal/safety advice beyond standard repair disclaimers.

## 6) Key User Flows
1. Symptom -> Diagnosis -> Part Suggestion -> Add to Cart handoff.
2. Model + Part Number -> Compatibility Answer.
3. "How do I install part X?" -> Installation steps + video/instruction references.
4. "I already bought this" -> order support routing + self-service links.

## 7) Customer Questions (Expanded)
Beyond the provided examples, customers commonly ask:

### A) Fit / Compatibility
- "Will `PS11750093` fit model `WDT780SAEM1`?"
- "Is there a revised replacement for my discontinued part?"
- "Does this part fit all Whirlpool WDT780 variants?"

### B) Troubleshooting / Diagnosis
- "Dishwasher fills then stops. What should I test first?"
- "Why is my dishwasher leaking only during rinse cycle?"
- "Fridge is warm but freezer is cold. Which parts are likely?"

### C) Selection / Substitutes
- "What’s the difference between OEM options for this symptom?"
- "Do I need left and right side kits or just one side?"

### D) Installation / Difficulty
- "How long does this repair usually take?"
- "What tools do I need for this part?"
- "Is this beginner-friendly or should I call a pro?"

### E) Pricing / Inventory / Shipping
- "Is this part in stock right now?"
- "What is fastest shipping to my ZIP?"
- "If this part is out of stock, what alternatives are compatible?"

### F) Post-Purchase
- "I ordered the wrong part. Can I return it?"
- "How do I find my order status?"
- "Part arrived damaged, what should I do?"

### G) Model Discovery / Clarification
- "Where is my model number located?"
- "I have a partial model string; can you find likely matches?"

## 8) UX Requirements
- Chat-first interface with rich product cards.
- Every recommendation includes: part name, part number, model fit status, price/stock snapshot, and source links.
- If confidence is low or model missing, ask clarifying questions before recommending.
- Visible "Why this part" explanation.
- Clear refusal pattern for out-of-scope prompts.

## 9) Functional Requirements
- Parse and index model pages, part pages, Q&A, symptoms, videos, and install instructions.
- Hybrid crawl strategy: pre-index core pages and use on-demand crawl fallback for missing/low-confidence/freshness-sensitive queries.
- Hybrid retrieval: keyword + semantic search.
- Structured compatibility lookup from normalized tables.
- Grounded response generation using only retrieved sources.
- Safety layer for unsupported/high-risk guidance.
- Analytics: intent, resolution, fallback, handoff.

## 10) Success Metrics
- Compatibility answer accuracy (human spot-check): >= 95%.
- First-response latency: <= 4s for cached model queries.
- Containment rate for in-scope intents: >= 70%.
- Click-through to product card/cart handoff.
- Fallback rate and hallucination incidence.

## 11) Demo Constraints
For demo, use local `gpt-oss` for response generation with a smaller, curated ingestion set (top dishwasher + refrigerator models) while keeping architecture production-extensible.
Use hybrid ingestion behavior:
- pre-crawl/index curated core set before demo,
- run targeted live crawl fallback only when needed,
- keep crawl output compact to avoid context pollution.

## 12) Risks
- Site structure changes can break parsers.
- Ambiguous symptom-language can produce low-confidence diagnosis.
- Inventory/price freshness needs frequent refresh.
