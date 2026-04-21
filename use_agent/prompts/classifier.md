# Cold Sales Classifier

Apply this classification to each candidate email.

## Input Fields

- `from`: sender name and email address
- `subject`: email subject line
- `body`: email body text (first message in thread only)
- `thread_replied`: boolean, true if thread already has a SENT message

## Classification Rules

Classify as `COLD_SALES` if the email is unsolicited outreach where
the primary goal is to sell a product, service, staffing, consulting,
or agency work to {{ organization }} or {{ user_name }} personally.

### Signal Scoring

STRONG signals (2 points each):

- `meeting_cta`: requests a call, meeting, demo, or "X minutes" of
  time
- `intro_formula`: "I'm your new...", "wanted to reach out", "just
  reaching out", "following up on my note", "circling back"
- `sender_domain`: non-corporate or outreach-tooling domain (`.info`,
  `.help`, `.live`, `.site`, `.pro`, `.space`, or `.org` from
  unknown organizations)
- `open_role_hook`: references a specific {{ organization }} job
  listing by role title
- `geo_arbitrage`: pitches LatAm, nearshore, offshore, Eastern
  Europe, or South Asia talent as a cost-saving alternative
- `candidate_pitch`: claims to have pre-vetted candidates or a
  shortlist for {{ organization }} specifically
- `event_invitation`: invites you to an in-person event, webinar,
  mixer, breakfast/lunch/dinner, happy hour, or "select group"
  gathering — especially when co-branded with a major vendor
  (Google, AWS, Microsoft, Salesforce, etc.). Event marketing by
  business development is almost always cold outreach dressed up
  as hospitality.

WEAK signals (1 point each):

- `flattery_hook`: references {{ user_name }}'s public writing,
  posts, talks, {{ organization }} press releases, job listings, or
  other public content to fake familiarity
- `crm_reference`: "our notes show", "last time we spoke", "you
  evaluated X in YYYY", "I noticed you use", scraped tech stack
  references
- `fake_thread`: subject prefixed with `Re:` or `Fwd:` but is
  clearly first contact
- `false_premise`: gets {{ organization }} facts wrong (ownership
  structure, tech stack, team size, or the right contact person)
- `urgency_pressure`: "act before", "limited spots", "closing the
  loop", "before end of [period]"
- `persistent_sequence`: "4th follow-up", "just wanted to confirm
  you saw", "in case my last email got buried"
- `sender_title`: "Sales Development Representative", "Account
  Executive", "Territory Manager", "Corporate Sales", "Business
  Development"

Classify as `COLD_SALES` if total score >= 3.

### Exceptions — do NOT classify as COLD_SALES

{% if vendor_names %}
- Email is from a current {{ organization }} vendor ({{ vendor_names | join(', ') }}) AND is about billing, renewal, or account issues — even if it includes upsell language
{% endif %}
- Email is a transactional notification, invoice, or receipt
- Email is a newsletter or digest the user subscribed to
- `thread_replied` is true
{% if safelist_domains %}
- Sender domain is one of the safelisted internal domains ({% for d in safelist_domains %}`{{ d }}`{% if not loop.last %}, {% endif %}{% endfor %})
{% endif %}

## Output

For each message, produce a JSON object with this shape:

```json
{
  "message_id": "<gmail message id>",
  "thread_id": "<gmail thread id>",
  "classification": "COLD_SALES" | "NOT_COLD_SALES",
  "score": 0,
  "signals": ["signal_name"],
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "response_mode": "hard_remove" | "hard_remove_with_correction" | "specific_decline" | "none",
  "correction_note": "<string or null>",
  "notes": "<brief reason, one sentence>"
}
```

### response_mode rules

- `hard_remove`: default for all `COLD_SALES`
- `hard_remove_with_correction`: `COLD_SALES` where `false_premise`
  signal fired; set `correction_note` to the specific false fact
- `specific_decline`: `COLD_SALES` from a known current vendor rep
  (not billing)
- `none`: `NOT_COLD_SALES`
