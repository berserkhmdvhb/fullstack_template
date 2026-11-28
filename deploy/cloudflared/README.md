# The Cloudflare Tunnel connector

The manifests of `cloudflared`, the connector that carries a cluster's public routes (the chart's
`edge.mode: tunnel`). One connector, one tunnel and one namespace per cluster. The
[Cloudflare setup guide](../../wiki/cloudflare-setup.md#83-run-the-connector-in-the-cluster) applies them
in order, after it has created the tunnel and stored its token in Secret Manager.

| File | What it is |
|---|---|
| `namespace.yaml` | the `cloudflared` namespace, Pod Security `restricted` |
| `priorityclass.yaml` | cluster-scoped; the connector outranks the app Pods on spot nodes |
| `externalsecret.yaml` | the token: Secret Manager `cloudflared-tunnel-token` into the Secret `tunnel-token`, through the namespace's `secret-manager` SecretStore ([`deploy/external-secrets/secretstore-secret-manager.yaml`](../external-secrets/secretstore-secret-manager.yaml), applied with `-n cloudflared`) |
| `deployment.yaml` | 2 replicas, image pinned by digest, read-only filesystem, no API token |
| `pdb.yaml` | at least one replica stays up during a drain |
| `networkpolicy.yaml` | no ingress; egress to DNS, the Cloudflare edge and the app namespaces only |

A rotated token needs `kubectl -n cloudflared rollout restart deployment/cloudflared`: the token reaches
the Pod as an environment variable. Move the image with each cloudflared release (Cloudflare supports a
release for one year): change the tag and the digest in `deployment.yaml` together.
