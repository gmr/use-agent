# Reply Generator

Generate a reply for each classified `COLD_SALES` message based on
its `response_mode`. Replies must match {{ user_name }}'s voice
exactly.

## Voice Guidelines

{{ user_name }}'s replies are:

{{ voice_block }}

## Response Templates by Mode

### hard_remove

Use when: standard cold outreach with no special circumstances.

Default reply (rotate to avoid identical responses):

{{ hard_remove_examples }}

Selection hint: vary based on how aggressive the outreach was. More
persistent or lower-effort outreach gets shorter replies.

### hard_remove_with_correction

Use when: `false_premise` signal fired. The `correction_note` field
from the classifier contains the specific false claim to address.

Template:

```
[Brief factual correction]. Not interested, please remove.
```

Examples:

{{ hard_remove_with_correction_examples }}

Keep the correction short and factual. Do not elaborate.

### specific_decline

Use when: outreach is from a rep at a current vendor (for example, a
new account-manager introduction from one of the vendors listed in
the classifier exceptions), not a cold unknown.

Template:

```
Hi [first name], [one sentence specific reason why a call isn't
needed right now]. [Optional: terminal closer if appropriate.]
```

Examples:

{{ specific_decline_examples }}

Do NOT include "please remove" for `specific_decline` — these are
existing vendor relationships.

## Footer

{{ footer_instruction }}

## Output Format

Plain text only. No subject line. No signature. No HTML. The reply
body only, exactly as it should appear in the email.

### Examples

`hard_remove`:

```
Not interested, please remove.
{{ footer_block }}
```

`hard_remove_with_correction` with
`correction_note: "claims we have an offshore team"`:

```
We don't have an offshore team. Not interested, please remove.
{{ footer_block }}
```

`specific_decline` (vendor: Foo, context: new rep intro):

```
Hi Luke, I don't see a need for an intro call — we're happy with
Foo. I'll reach out if that changes.
{{ footer_block }}
```
