# n8n integration

`integrations/n8n/flowpilot-pending-response.json` is an inactive, credential-free
n8n workflow export. Import it into a local n8n instance and start FlowPilot's
API on `127.0.0.1:8000`.

The workflow accepts a POST body shaped like:

```json
{"message": "My order ORD1042 has not arrived."}
```

It calls only `POST /workflow` in template mode and returns that result to the
webhook caller. The API response must remain `Pending` with `can_proceed` false
until an authorized human reviews it through FlowPilot's review endpoint or
Streamlit interface. The workflow does not send responses to customers and
does not call refund, payment, order mutation, or approval endpoints.

The webhook is not a production authentication boundary. Keep this workflow
inactive and local unless you add appropriate authentication, TLS, and
deployment-specific network controls. When n8n runs in a container, update the
HTTP Request node's local API URL to an address reachable from that container.
No n8n runtime was available here, so import/activation behavior remains
UNVERIFIED.
