# Easy Admin AI Cash Book Allocation

This release adds reviewable OpenAI account-allocation suggestions to Accounting > Cash Book. It does not give the AI permission or code paths to post the cash book.

## OpenAI setup

1. Use the dedicated Easy Admin OpenAI project and service account.
2. Restrict the service-account key to these permissions:
   - List models: Read
   - Responses (`/v1/responses`): Write
   - All other permissions: None
3. In Render, add `OPENAI_API_KEY` as a secret environment variable. Never paste the key into Easy Admin or commit it to the application files.
4. Keep the project key in one Render environment only. Rotate it in OpenAI and Render if it is ever exposed.

The application uses the OpenAI Responses API with Structured Outputs and sets `store: false`. The default model is `gpt-5.4-mini`.

## Render environment variables

Required:

- `OPENAI_API_KEY`: the Easy Admin service-account secret key.

Included defaults in `render.yaml`:

- `EASYADMIN_OPENAI_MODEL=gpt-5.4-mini`
- `EASYADMIN_AI_MAX_LINES_PER_RUN=100`
- `EASYADMIN_OPENAI_TIMEOUT_SECONDS=60`

Deploy the complete application package. Existing database tables are migrated automatically when the application starts. Take the normal production database backup before deployment.

## Enable the feature

1. Sign in as Super Admin.
2. Open Super Admin Controls > AI Configuration.
3. Enter the OpenAI project name, project ID, service-account name and model. These are non-secret administration references; the API key remains in Render.
4. Select Save Configuration while global access is disabled.
5. Select Test Connection. The test confirms that the key can read the selected model.
6. Enable Easy Admin AI globally and save.
7. Open Super Admin Controls > Tenants, edit a company, enable Accounting, and then enable AI Cash Book Allocation.

Both the global switch and the company switch must be enabled. Only a Super Admin can change either switch.

## Accountant workflow

1. Upload or open a draft bank statement in Accounting > Cash Book.
2. Select Suggest with AI.
3. Review every suggested Chart of Accounts allocation. An AI confidence badge and short reason are shown on suggested rows. Uncertain rows remain unallocated.
4. Change or clear any allocation as required.
5. Select Save Allocations. This records the human review as accepted, changed or cleared.
6. Select Post Cash Book only after the review is complete.

AI suggestions never create journals. The server rejects posting while any AI suggestion is still pending review.

## Data and safety controls

- Processing is limited to active Accounting tenants and draft, unallocated cash-book lines.
- Only the transaction date, description, direction and amount are sent for classification, together with active Chart of Accounts choices and a small set of similar allocations from that same tenant's posted history.
- The bank account itself is excluded from the allowed allocation accounts.
- Model output is checked against the tenant, batch, line IDs and active Chart of Accounts before saving.
- Suggestions below 60% confidence remain unallocated.
- An AI result cannot overwrite a line that a user allocated or edited while the request was running.
- Each run records its requester, model, counts, OpenAI response ID, token usage, status and error summary for auditing. The API key and bank data are not written to these audit records.
- Requests are rate limited and capped at 100 lines by default.

## Disable or roll back

- Disable one tenant in Super Admin Controls > Tenants.
- Disable all tenants in Super Admin Controls > AI Configuration.
- For an immediate credential stop, remove or rotate `OPENAI_API_KEY` in Render and restart the service.

Disabling AI does not remove prior human-reviewed allocations or posted accounting records.

## Official OpenAI references

- Responses API: https://developers.openai.com/api/reference/cli/resources/responses/methods/create
- GPT-5.4 Mini: https://developers.openai.com/api/docs/models/gpt-5.4-mini
