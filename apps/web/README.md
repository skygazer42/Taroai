# Taroai Workspace

Static workspace for the local cloud PoC.

```bash
cd apps/web
python3 -m http.server 3000 --directory .
```

Open `http://localhost:3000` and point the API field at the FastAPI service.
For browser-controller and Compose smoke checks, the connection strip can also
be prefilled through URL parameters such as
`http://localhost:3000/?apiBase=http%3A%2F%2Flocalhost%3A8000&tenantId=tenant_acme&userId=user_luke&workspaceId=workspace_sales&email=owner%40example.com`.
The workspace intentionally ignores and removes URL `accessToken`, `token`, and
`password` parameters so credentials are not persisted in browser history.

For the default local cloud PoC configuration, the connection strip can also
bootstrap the first tenant: enter the tenant slug, owner name, owner
email/password, and local bootstrap token, then click `Bootstrap`. The bootstrap
token is sent once in `X-Bootstrap-Token`, is never saved to `localStorage` or
`sessionStorage`, and is cleared from the input after the request. The workspace
then logs in with the owner email/password, stores only the returned Bearer token
in `sessionStorage`, and uses it for API calls. Dev headers remain available
when the API is explicitly started with
`TAROAI_DEV_REQUEST_HEADERS_ENABLED=true`.
