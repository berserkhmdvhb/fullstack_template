# Cloudflare setup: domain, edge and secure access

This guide puts a domain in front of the platform with Cloudflare. It covers:
- registering the domain;
- a hardened zone, with TLS, DNSSEC and anti-spoofing records;
- a **Cloudflare Tunnel** from the GKE cluster, so the cluster has no public entry point;
- **Cloudflare Access**, to keep previews private;
- scoped, expiring API tokens for CI;
- an optional private **R2** bucket for off-site backups.

It follows [GKE Cluster Setup](./gke-cluster-setup.md). Finish that one first: section 8 here needs the cluster. [Platform Setup](./platform-setup.md) gives the overall order.

Every step ends with a **Check**. Don't move on until it passes.

| | |
|---|---|
| Time | 45 to 75 minutes. DNSSEC can take up to a day to go active, but nothing else waits on it. |
| Cost | the domain, about $10 to $20 a year depending on the TLD, sold at cost. Everything else here is on Cloudflare's free tiers. |
| You need | a Cloudflare account, the GKE cluster from the previous guide, and `curl`, `dig`, `openssl`, `jq`, and Docker with buildx |
| Written | 2026-11-20; checked against Cloudflare's docs on 2026-11-25. Dashboard menus move around, so if a path has changed, search the dashboard for the setting's name. |

## Contents

1. [What you will build](#1-what-you-will-build)
2. [Before you start](#2-before-you-start)
3. [Set your variables once](#3-set-your-variables-once)
4. [Secure the Cloudflare account](#4-secure-the-cloudflare-account)
5. [Register the domain](#5-register-the-domain)
6. [Zone baseline: TLS, DNSSEC and anti-spoofing](#6-zone-baseline-tls-dnssec-and-anti-spoofing)
7. [API tokens: scoped, expiring and tested](#7-api-tokens-scoped-expiring-and-tested)
8. [Cloudflare Tunnel into GKE](#8-cloudflare-tunnel-into-gke)
9. [Keep previews private with Cloudflare Access](#9-keep-previews-private-with-cloudflare-access)
10. [Static hosting for your site: prove the deploy token](#10-static-hosting-for-your-site-prove-the-deploy-token)
11. [HSTS, once everything is HTTPS](#11-hsts-once-everything-is-https)
12. [Optional: a private R2 bucket for off-site backups](#12-optional-a-private-r2-bucket-for-off-site-backups)
13. [Final checklist and the values to hand over](#13-final-checklist-and-the-values-to-hand-over)
14. [Costs](#14-costs)
15. [Troubleshooting](#15-troubleshooting)
16. [What comes next](#16-what-comes-next)

---

## 1. What you will build

> **Do now.**

```text
 Visitors
    │  HTTPS only (TLS 1.2+), DNSSEC-signed DNS, WAF managed rules
    ▼
 Cloudflare edge  ─── DNS for <domain>, edge certificates, cache, DDoS protection
    │
    ├── <domain>, www           → your static site (Workers static assets)        [at publish]
    ├── preview.<domain>        → your site's previews, behind Cloudflare Access  [now]
    │
    └── service hostnames ───►  Cloudflare Tunnel ◄─── outbound only ─── cloudflared (2 Pods)
                                                                           in GKE "apps-dev"
                                                                               │ in-cluster
                                                                               ▼
                                                                         app Services

 Off-site backups ──► R2 bucket (private, no public URL, lifecycle-expired)          [optional]
```

**Why this design:**
- **No public origin.** `cloudflared` dials out to Cloudflare, so the cluster has no load balancer, no public IP and no open inbound port to attack. The only way in is through Cloudflare.
- **One home for DNS and the edge.** The domain, its certificates, the WAF and the access control are all in one place, under one audit log.
- **Private by default.** Anything not yet published sits behind Cloudflare Access, so only you can see it.
- **Least privilege, the same as on GCP.** Every CI token can do one job, on one account or zone, and it expires.

The alternative to the Tunnel is a proxied DNS record that points at the GKE Gateway's load balancer IP. That's a valid design too: it uses the Gateway API end to end. Its costs:
- the load balancer, at about $18 to $20 a month;
- origin TLS through Google Certificate Manager with DNS authorisation;
- a Cloud Armor allowlist of Cloudflare's IP ranges, so nobody can bypass Cloudflare.

The chart supports both (`edge.mode: gateway`, the default, or `edge.mode: tunnel`; see [`values.yaml`](../charts/your-service-name/values.yaml), the Edge section). This guide sets up the Tunnel, because it's cheaper and exposes nothing.

## 2. Before you start

> **Do now.**

### 2.1 Tools

| Tool | Install | Check |
|---|---|---|
| curl, openssl | your package manager | `curl --version; openssl version` |
| jq, for sections 5 and 12 | your package manager | `jq --version` |
| Docker with buildx, for the digest lookup of 8.2 | <https://docs.docker.com/engine/install/> | `docker buildx version` |
| dig | `sudo apt install dnsutils` (Debian/Ubuntu), `bind-utils` (Fedora) | `dig -v` |
| kubectl, with the GKE kubeconfig | from the GKE guide | `source ~/gke-setup.env && kubectl get nodes` |
| Node.js 26+, only for section 10 | <https://nodejs.org> or your version manager | `node --version` |
| AWS CLI v2, only for section 12 | <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html> | `aws --version` |

### 2.2 The hostname plan

Decide these now. The rest of the guide uses them.

| Hostname | Serves | When |
|---|---|---|
| `<domain>`, `www.<domain>` | your public site | at publish |
| `preview.<domain>` | your site's previews, behind Access | now |
| `hello.<domain>` | a temporary test service for this guide | deleted at the end of section 9 |
| `<SERVICE_NAME>-dev.<domain>`, `<SERVICE_NAME>-qa.<domain>`, `<SERVICE_NAME>.<domain>` | the template app's dev, qa and production environments: one host each, the `edge.hosts.root` of each environment's values (the whole app on one origin; an optional `<SERVICE_NAME>-api[-<env>].<domain>` is a backend-only host, see [Infrastructure](./infrastructure.md#edge-hostnames)). Always one label under the zone, so the free Universal SSL certificate covers it. Dev and qa stay behind Access; production is public when you publish | when the pipeline deploys it |
| `<service>-pr-<N>.<preview-zone>` | optional pull request previews of the template app, behind Access | only if you turn previews on ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)) |

Your site's `preview.<domain>` and the app's pull request previews are different things. The app's previews need one hostname per pull request, each one label under a zone: the free Universal SSL certificate covers `<zone>` and `*.<zone>`, never a second level such as `*.preview.<domain>`.

Each hostname is one DNS record, the tunnel routes that the chart's NOTES print for it (8.4), and, while it isn't public, one Access application (9).

Buying the domain now just reserves the name. Pointing the apex at a live site is the publish step.

## 3. Set your variables once

> **Do now.**

Identifiers such as the account ID and zone ID aren't secrets. Keep them out of committed files and docs anyway, because they make targeted mistakes and attacks easier. Tokens never go into this file at all.

```bash
cat > ~/cloudflare-setup.env <<'EOF'
export DOMAIN="your-domain.example"  # your registered domain
export CF_ACCOUNT_ID=""              # filled in section 5
export CF_ZONE_ID=""                 # filled in section 5
export PREVIEW_DOMAIN=""             # only for pull request previews (9.2): the previews' own zone
EOF
source ~/cloudflare-setup.env
```

To use a token in a shell without it landing in your history or on screen, read it like this:

```bash
read -rs CF_API_TOKEN && export CF_API_TOKEN   # paste the token, press Enter; nothing is shown
# ... use it ...
unset CF_API_TOKEN
```

**Check:** `source ~/cloudflare-setup.env && echo "$DOMAIN"` prints your domain.

## 4. Secure the Cloudflare account

> **Do now.**

The account controls your DNS, so anyone who takes it over can redirect all your traffic.

1. **Turn on two-factor authentication** under **My Profile → Authentication**. Use a security key or passkey if you have one, otherwise an authenticator app.
2. **Save the backup codes** somewhere offline, such as a password manager or paper. Don't keep them in this repo or on this machine in plain text.
3. **Check who has access.** Under **Manage Account → Members** you should be the only member.
4. **Know where the audit log is:** **Manage Account → Audit Log**. Every change in this guide shows up there.

**Check:** sign out and back in. You're asked for the second factor.

## 5. Register the domain

> **Do now.**

1. Open **Domain Registration → Register Domains** and search for a name. Cloudflare charges the registry's price, with no markup.
2. Buy it with these settings:
   - **auto-renew on**;
   - **WHOIS redaction**, which is on by default;
   - **transfer lock**, also on by default.
3. Cloudflare creates the zone and uses its own nameservers automatically. Its registrar requires its own DNS.
4. Copy the **Zone ID** and **Account ID** from the zone's **Overview** page, in the right-hand column, into `~/cloudflare-setup.env`.

If you already own the domain elsewhere, add it to Cloudflare as a site, change the nameservers at your current registrar, and optionally transfer it to Cloudflare once the zone is active.

**Check:**

```bash
source ~/cloudflare-setup.env
dig +short NS "$DOMAIN"
# → two names ending in .ns.cloudflare.com
curl -fsSL "https://rdap.org/domain/$DOMAIN" \
  | jq -r '.entities[]? | select(.roles | index("registrar")) | .vcardArray[1][] | select(.[0] == "fn") | .[3]'
# → Cloudflare, Inc.   (empty: the registry's RDAP layout differs; the dashboard's Domain Registration page is the fallback)
```

## 6. Zone baseline: TLS, DNSSEC and anti-spoofing

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS (section 11) stays a staged, manual rollout.

Make all of these settings in the zone.

### 6.1 TLS

| Setting | Where | Value | Why |
|---|---|---|---|
| Encryption mode | SSL/TLS → Overview → Configure → **Custom SSL/TLS** | **Full (strict)** | Cloudflare always validates the origin's certificate, so there's no silent downgrade. Tunnel origins satisfy this automatically. New zones start in **Automatic SSL/TLS**, where Cloudflare picks the mode. Custom pins it, so nothing changes it behind your back and the infrastructure code can state it. |
| Always Use HTTPS | SSL/TLS → Edge Certificates | **On** | Plain HTTP is redirected. |
| Minimum TLS version | SSL/TLS → Edge Certificates | **TLS 1.2** | Drops the broken protocol versions. |
| TLS 1.3 | SSL/TLS → Edge Certificates | **On** | Faster and safer. |
| Automatic HTTPS Rewrites | SSL/TLS → Edge Certificates | **On** | Fixes stray `http://` links. |
| HSTS | SSL/TLS → Edge Certificates | **leave off for now** | Turned on in [section 11](#11-hsts-once-everything-is-https). |

Writing the encryption mode down here, and checking it in section 8, means nobody has to guess what it is.

### 6.2 DNSSEC

DNSSEC protects visitors from forged DNS answers. Under **DNS → Settings → DNSSEC**, click **Enable DNSSEC**. With Cloudflare as registrar, the DS record is published for you.

### 6.3 Records that stop spoofing and mis-issuance

Under **DNS → Records**, add these. If you set up email forwarding later, see the note below the table.

| Type | Name | Content | Why |
|---|---|---|---|
| CAA | `@` | `0 issue "pki.goog"` | Lets Google issue certificates, which you'd need if you ever use the Gateway option. Cloudflare adds CAA entries for its own certificate authorities automatically. |
| CAA | `@` | `0 iodef "mailto:<you>"` | Where certificate authorities report refused requests. |
| TXT | `@` | `v=spf1 -all` | "This domain sends no email", so nobody can send mail as it. |
| TXT | `_dmarc` | `v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s` | Receivers reject any mail that claims to come from it. |

If you turn on **Email Routing**, for example to forward `hello@<domain>` to your inbox, Cloudflare adds MX records and offers its own SPF record. Accept its SPF value, `v=spf1 include:_spf.mx.cloudflare.net ~all`, in place of `-all`, and keep the DMARC record.

### 6.4 Security defaults

- **Security → WAF → Managed rules:** make sure the **Cloudflare Free Managed Ruleset** is on.
- **Bot Fight Mode:** leave it **off** for now. It challenges automated clients, and on the free plan you can't exempt your own health checks or API clients from it.

**Check:**

```bash
dig +short CAA "$DOMAIN"                 # → your CAA lines, plus Cloudflare's own
dig +short TXT "$DOMAIN"                 # → "v=spf1 -all" (or Cloudflare's SPF if you use Email Routing)
dig +short TXT "_dmarc.$DOMAIN"          # → "v=DMARC1; p=reject; ..."
dig +short DS "$DOMAIN" @1.1.1.1         # → a DS record. It can take a few hours, and up to a day, to appear.
dig +dnssec "$DOMAIN" SOA @1.1.1.1 | grep -E '^;; flags:'   # → the flags include "ad" once DNSSEC is active
```

The TLS settings are checked against a real hostname in section 8.5.

## 7. API tokens: scoped, expiring and tested

> **Do now.** The tokens are made by hand for good. They are used by your site repository's deploy; no template workflow reads a Cloudflare token.

Cloudflare doesn't yet offer keyless login from GitHub Actions the way Google's Workload Identity Federation does, so CI needs tokens. Four rules keep them safe:
1. **One token per job.** A token can do one thing, on one account or zone.
2. **Every token expires.** Set a TTL of, for example, 12 months, and put a renewal reminder in your calendar a week before it.
3. **Prove each token against the endpoint it will actually call.** A token can verify as "active" and still be refused (`403`) by the API it was made for, depending on its owner type and permissions.
4. **Keep tokens in GitHub as environment secrets.** Each token belongs to the GitHub environment that uses it, for example `production`, so only jobs that passed that environment's approval can read it. A repository-wide secret would be readable by every workflow.

### 7.1 The tokens you need

| Token | Owner | Permissions | Used by |
|---|---|---|---|
| `ci-site-deploy` | account | Account: **Workers Scripts: Edit**, **Account Settings: Read**. Zone `<domain>`: **Workers Routes: Edit**, **DNS: Edit**, **Zone: Read** | your site's deploys (section 10 and its pipeline) |
| R2 backup credentials | account | **Object Read & Write** on the backup bucket only | the backup jobs (section 12) |
| `tf-cloudflare` (later) | account | the zone's DNS, Zone Settings and SSL settings, the account's Tunnel and Access, and R2 | the infrastructure-as-code step ([section 16](#16-what-comes-next)) |

The Tunnel token from section 8 isn't an API token. It's a connector secret that lives only in the cluster.

### 7.2 Create `ci-site-deploy`

1. Go to **Manage Account → Account API Tokens → Create Token → Custom token**. Account-owned tokens keep working if your user changes.
2. Add the permissions from 7.1. Under **Account Resources**, choose your account; under **Zone Resources**, choose **Specific zone → `<domain>`**.
3. Set the **TTL** end date to 12 months from today.
4. Create it, then copy the token once. Cloudflare never shows it again.

**Check that it's active:**

```bash
source ~/cloudflare-setup.env
read -rs CF_API_TOKEN && export CF_API_TOKEN
curl -fsS "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/tokens/verify" \
  -H "Authorization: Bearer $CF_API_TOKEN" | python3 -c 'import json,sys; r=json.load(sys.stdin)["result"]; print(r["status"], r.get("expires_on"))'
# → active  <the end date you set>
```

Keep `CF_API_TOKEN` set for section 10, where the token is proved against the deploy API itself.

## 8. Cloudflare Tunnel into GKE

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. This covers 8.1 (the tunnel), 8.4 (routes and CNAMEs); a tunnel made in the dashboard has to be imported.

> **Not in the template.** Section 8.3's connector (the `cloudflared` namespace, Secret, Deployment and NetworkPolicy) is not shipped by this repository; you apply it by hand as shown. The chart's NetworkPolicy, in `edge.mode: tunnel`, admits the namespace `edge.namespaceSelector` selects (empty means `kubernetes.io/metadata.name: cloudflared`), so the namespace must match. 8.2, 8.5 and 8.6 are **Do now**.

### 8.1 Create the tunnel

1. Open **Zero Trust**. The first time, pick a **team name** and the **Free** plan. It may ask for a payment method even on the free plan; you won't be charged.
2. Go to **Zero Trust → Networks → Connectors → Cloudflare Tunnels → Create a tunnel → Cloudflared** (the main dashboard also lists tunnels under **Networking → Tunnels**), and name it `apps-dev`.
3. On the install screen, copy the **token**: the long string after `--token` in any of the install commands. Don't run those commands; the cluster runs the connector instead.

### 8.2 Pin the connector image

Pick the newest release from <https://github.com/cloudflare/cloudflared/releases>, then pin it by digest:

```bash
export CLOUDFLARED_VERSION="<latest release, e.g. 2026.x.y>"
export CLOUDFLARED_DIGEST="$(docker buildx imagetools inspect "cloudflare/cloudflared:$CLOUDFLARED_VERSION" --format '{{.Manifest.Digest}}')"
export CLOUDFLARED_IMAGE="cloudflare/cloudflared:$CLOUDFLARED_VERSION@$CLOUDFLARED_DIGEST"
echo "$CLOUDFLARED_IMAGE"
```

**Check:** it prints `cloudflare/cloudflared:<version>@sha256:<64 hex>`.

### 8.3 Run the connector in the cluster

The connector runs as 2 replicas spread over the nodes, so one node going down doesn't cut the tunnel. It uses the `restricted` Pod Security profile, a read-only filesystem, no Kubernetes API token and a disruption budget.

```bash
source ~/gke-setup.env      # the GKE kubeconfig

kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: cloudflared
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/warn: restricted
EOF

# Store the tunnel token without echoing it, and without putting it in a process's arguments:
read -rs TUNNEL_TOKEN
printf '%s' "$TUNNEL_TOKEN" | kubectl -n cloudflared create secret generic tunnel-token --from-file=token=/dev/stdin
unset TUNNEL_TOKEN

kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared
  namespace: cloudflared
spec:
  replicas: 2
  selector:
    matchLabels: { app: cloudflared }
  template:
    metadata:
      labels: { app: cloudflared }
    spec:
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: { app: cloudflared }
      containers:
        - name: cloudflared
          image: ${CLOUDFLARED_IMAGE}
          args: ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"]
          env:
            - name: TUNNEL_TOKEN
              valueFrom:
                secretKeyRef: { name: tunnel-token, key: token }
          ports:
            - { name: metrics, containerPort: 2000 }
          readinessProbe:
            httpGet: { path: /ready, port: metrics }
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /ready, port: metrics }
            initialDelaySeconds: 30
            periodSeconds: 15
            failureThreshold: 8
          resources:
            requests: { cpu: 50m, memory: 64Mi }
            limits: { memory: 256Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: cloudflared
  namespace: cloudflared
spec:
  minAvailable: 1
  selector:
    matchLabels: { app: cloudflared }
EOF

kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
```

**Check:**

```bash
kubectl -n cloudflared get pods -o wide
# → 2 Pods Running and Ready, ideally on different nodes
kubectl -n cloudflared logs deployment/cloudflared | grep -m 4 'Registered tunnel connection'
# → up to 4 lines, one per connection to a nearby Cloudflare data centre
kubectl get svc -A --field-selector spec.type=LoadBalancer
# → "No resources found": the cluster has no public entry point
```

In the dashboard, the tunnel `apps-dev` shows **Healthy**. Warnings about ICMP or UDP buffer sizes in the logs are harmless.

Now close the connector's network. Nothing may connect to it, and it may reach only DNS, the Cloudflare edge and the app namespaces:

```bash
kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cloudflared
  namespace: cloudflared
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
  ingress: []
  egress:
    - to:
        - namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: kube-system}}
          podSelector: {matchLabels: {k8s-app: kube-dns}}
      ports:
        - {protocol: UDP, port: 53}
        - {protocol: TCP, port: 53}
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except: [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16]
      ports:
        - {protocol: UDP, port: 7844}
        - {protocol: TCP, port: 7844}
        - {protocol: TCP, port: 443}
    - to:
        - namespaceSelector:
            matchExpressions:
              - {key: tier, operator: In, values: [app-ns, preview-app]}
EOF
```

- Port 7844 is the tunnel: QUIC over UDP, or HTTP/2 over TCP.
- Port 443 is for cloudflared's optional features.
- The last rule is the app namespaces. The bootstrap labels them `tier: app-ns`, and previews `tier: preview-app`. Label any other namespace the tunnel serves `tier=app-ns`, or add it here.

**Check:**

```bash
kubectl -n cloudflared rollout restart deployment/cloudflared
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
kubectl -n cloudflared logs deployment/cloudflared --since=3m | grep -c 'Registered tunnel connection'
# → 1 or more: the connector still reaches Cloudflare
POD_IP="$(kubectl -n cloudflared get pod -l app=cloudflared -o jsonpath='{.items[0].status.podIP}')"
kubectl -n default run np-probe --rm -i --restart=Never --image=busybox:1.37 -- \
  wget -qO- -T 5 "http://$POD_IP:2000/ready"; echo "exit=$?"
# → a timeout and a non-zero exit=: nothing in the cluster can reach the connector
```

### 8.4 Route a test hostname through the tunnel

```bash
kubectl create namespace edge-test
kubectl label namespace edge-test tier=app-ns
kubectl -n edge-test create deployment hello --image=nginxinc/nginx-unprivileged:stable-alpine --port=8080
kubectl -n edge-test expose deployment hello --port=8080
kubectl -n edge-test rollout status deployment/hello --timeout=180s
```

In **Zero Trust → Networks → Connectors → Cloudflare Tunnels → `apps-dev` → Edit → Published application routes → Add a published application route** (older dashboards: **Public Hostname → Add**), add the route below.

| Field | Value |
|---|---|
| Subdomain | `hello` |
| Domain | `<domain>` |
| Service type | `HTTP` |
| URL | `hello.edge-test.svc.cluster.local:8080` |

Cloudflare creates the proxied DNS record for you.

**The service's own routes.** Deploy the chart with `edge.mode: tunnel`. It renders no Ingress or
HTTPRoute; its NOTES print the route table built from the chart's one routes list (`edge.routes`,
[Infrastructure, Edge](./infrastructure.md#the-routes-list)): one line per hostname and path, with the
in-cluster URL. Add one public hostname per line, in that order, with the catch-all `/` last. The
chart's NetworkPolicy admits the `cloudflared` namespace by default (`edge.namespaceSelector`
changes it); Cloudflare terminates TLS, and the hop to the Service is plain HTTP.

### 8.5 Check the edge, end to end

```bash
H="hello.$DOMAIN"
dig +short "$H"
# → Cloudflare edge IPs (104.x.x.x / 172.6x.x.x), never a Google IP

curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "http://$H"
# → 301 https://hello.<domain>/          (Always Use HTTPS)

curl -sS -I "https://$H" | grep -iE '^(HTTP/|server:)'
# → HTTP/2 200   and   server: cloudflare

curl -sS -o /dev/null -w '%{http_code}\n' --tlsv1.2 --tls-max 1.2 "https://$H"
# → 200   (TLS 1.2 is accepted)

# TLS 1.1 must be refused by Cloudflare, not by your own client. Modern OpenSSL won't
# offer TLS 1.1 at its default security level, so lower it for this one test:
out="$(echo | openssl s_client -connect "$H:443" -servername "$H" -tls1_1 -cipher 'DEFAULT:@SECLEVEL=0' 2>&1)"
case "$out" in
  *"alert protocol version"*|*"alert handshake failure"*) echo "TLS 1.1 refused by Cloudflare (right)" ;;
  *"New, TLSv1.1, Cipher is"*) echo "TLS 1.1 ACCEPTED (wrong)" ;;
  *) echo "inconclusive: your OpenSSL couldn't offer TLS 1.1; check the setting in the dashboard"; printf '%s\n' "$out" | tail -n 3 ;;
esac
# → TLS 1.1 refused by Cloudflare (right)

echo | openssl s_client -connect "$H:443" -servername "$H" 2>/dev/null | openssl x509 -noout -issuer -enddate
# → an issuer such as Google Trust Services or Let's Encrypt, and a future end date
```

**Check:** all six outputs match the comments. Keep `hello` running for section 9.

### 8.6 Keep the connector current

> **Do now.**

Cloudflare supports each cloudflared release for one year from its release date, so check <https://github.com/cloudflare/cloudflared/releases> monthly. To upgrade, rerun 8.2 with the new version, then:

```bash
kubectl -n cloudflared set image deployment/cloudflared cloudflared="$CLOUDFLARED_IMAGE"
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
```

The two replicas and the disruption budget keep the tunnel up while it rolls. Keep 8.3's manifests, with the pinned image, as files in your platform's repository rather than only in shell history, so the version in use is recorded. The platform's infrastructure-as-code repository is meant to own them later.

**Check:** `kubectl -n cloudflared get deployment cloudflared -o jsonpath='{.spec.template.spec.containers[0].image}'` prints the new image, and the tunnel shows **Healthy** in the dashboard.

## 9. Keep previews private with Cloudflare Access

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. Access apps and policies can be kept in code.

> **Note both values.** The backend's `AUTH_MODE=cloudflare-access` needs your Zero Trust team domain and the application's AUD tag (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`: [Environment Variables](./environment-variables.md), [Security](./security.md#authentication--authorization)). The team domain is `<team>.cloudflareaccess.com`, with the team name you picked in 8.1. The AUD tag is on the application: newer dashboards show it on its **Additional settings** tab, older ones on its **Overview** tab.

Cloudflare Access puts a login in front of a hostname. It's free for up to 50 users. Use it for every hostname that isn't public yet.

1. Go to **Zero Trust → Access controls → Applications → Add an application → Self-hosted** (some dashboards say **Self-hosted and private**, then **Add public hostname**).
2. Name it `hello (test)`, with application domain `hello.<domain>`.
3. Add a policy named `owner` with action **Allow**, and an include rule of **Emails → your address**.
4. **Login method:** a new Zero Trust organisation starts with the **Cloudflare identity provider**, so you sign in with your Cloudflare account, and one-time PIN is no longer added for you. To use One-time PIN or GitHub instead, add it under **Zero Trust → Integrations → Identity providers**.
5. Save.

**Check:**

```bash
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://hello.$DOMAIN"
# → 302 https://<team>.cloudflareaccess.com/...    (anonymous visitors are sent to the login)
```

In a browser, open `https://hello.<domain>`, log in with the method you set up, and you see the nginx page. That's the whole flow working.

Now create the real one: an Access application for `preview.<domain>`, with the same `owner` policy. Then remove the test:
1. delete the `hello (test)` Access application;
2. delete the `hello` public hostname in the tunnel;
3. delete the test namespace:

```bash
kubectl delete namespace edge-test
dig +short "hello.$DOMAIN"     # → nothing, once the record is gone
```

### 9.2 Optional: the previews' hostnames, Access application and service token

> **Only if you turn previews on** with public hostnames ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)), after [GKE 12.5](./gke-cluster-setup.md#125-optional-pull-request-previews).

Use a zone of its own for previews (a second domain, with the section 6 baseline): its wildcard Access application then covers previews only. On your main zone, the wildcard would also cover every hostname that has no Access application of its own, `www` included. `PREVIEW_DOMAIN` is that zone. Each preview has one hostname, one label under it, which the free Universal SSL certificate covers (2.2).

1. **The service token.** Go to **Zero Trust → Access controls → Service credentials → Service Tokens → Create Service Token**, named `preview-smoke`, with a duration of one year and a renewal reminder (section 7's rule 2). Copy the **Client ID** and **Client Secret**; the secret is shown once. In GitHub, add them as the secrets `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` of the `preview` environment.
2. **The Access application,** before the route, so no preview is ever public. **Add an application → Self-hosted**, named `previews`, with public hostname subdomain `*` and domain `<PREVIEW_DOMAIN>`. Add two policies: `owner` (**Allow**, your email, as in 9) and `preview-smoke` (action **Service Auth**, include **Service Token → `preview-smoke`**). Service Auth matters: with **Allow**, Access sends the smoke test to a login page.
3. **The route.** In the tunnel `apps-dev`'s **Published application routes**, add subdomain `*`, domain `<PREVIEW_DOMAIN>`, service **HTTP**, URL `<PREVIEW_GW_IP>:80` (GKE 12.5). Keep it last in the list: routes match in order. If the dashboard doesn't create the DNS record, add a proxied `CNAME` `*` → `<TUNNEL_ID>.cfargotunnel.com` (the tunnel ID is on the tunnel's page).

**Check,** once a preview is deployed:

```bash
source ~/cloudflare-setup.env
[ -n "$PREVIEW_DOMAIN" ] || { echo "set PREVIEW_DOMAIN in ~/cloudflare-setup.env (9.2)"; exit 1; }
P="<SERVICE_NAME>-pr-<N>.$PREVIEW_DOMAIN"
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://$P"
# → 302 https://<team>.cloudflareaccess.com/...   (anonymous visitors go to the login)
read -rs CF_ACCESS_CLIENT_ID && read -rs CF_ACCESS_CLIENT_SECRET
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H @<(printf 'CF-Access-Client-Id: %s\nCF-Access-Client-Secret: %s\n' "$CF_ACCESS_CLIENT_ID" "$CF_ACCESS_CLIENT_SECRET") \
  "https://$P"
# → 200   (the service token passes; the secret never reaches a command line)
unset CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET
```

## 10. Static hosting for your site: prove the deploy token

> **Do now.**

Cloudflare's current recommendation for new static sites is **Workers with static assets**; Pages still works. Requests for static assets are free and unlimited. The real deploy of your site comes with its pipeline. This step only proves that the `ci-site-deploy` token from section 7 can deploy, by publishing and then deleting a one-page site on your `workers.dev` subdomain.

If you've never used Workers, open **Workers & Pages** once and choose your `workers.dev` subdomain.

```bash
source ~/cloudflare-setup.env
# CF_API_TOKEN is still set from 7.2. If not: read -rs CF_API_TOKEN && export CF_API_TOKEN
export CLOUDFLARE_API_TOKEN="$CF_API_TOKEN" CLOUDFLARE_ACCOUNT_ID="$CF_ACCOUNT_ID"

work="$(mktemp -d)" && mkdir -p "$work/public"
echo '<!doctype html><title>deploy check</title><p>ok</p>' > "$work/public/index.html"
cat > "$work/wrangler.jsonc" <<EOF
{
  "name": "site-deploy-check",
  "compatibility_date": "$(date -u -d '-1 day' +%F 2>/dev/null || date -u -v-1d +%F)",
  "assets": { "directory": "./public" }
}
EOF
( cd "$work" && npx --yes wrangler@4.127.1 deploy )
```

The `compatibility_date` is set to yesterday, in UTC. Wrangler rejects a date in the future.

**Check:** wrangler prints a URL like `https://site-deploy-check.<subdomain>.workers.dev`. Then:

```bash
curl -sS "https://site-deploy-check.<subdomain>.workers.dev" | grep -o '<p>ok</p>'   # → <p>ok</p>
( cd "$work" && npx --yes wrangler@4.127.1 delete --name site-deploy-check --force )
rm -rf "$work"; unset CLOUDFLARE_API_TOKEN CF_API_TOKEN
```

The Wrangler version is pinned exactly here; pin it the same way in your site's CI (no template workflow runs Wrangler), so a new release can't change a deploy without you noticing.

If the deploy fails with `403` or `Authentication error`, the token lacks a permission. Compare it with 7.1. This is exactly the failure section 7's rule 3 is meant to catch before CI hits it.

### Put the token where CI can use it

> This is your **site repository's** setting, not the template's: no template workflow reads `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_ACCOUNT_ID` (see [Platform Setup](./platform-setup.md#used-by-a-site-repository-not-by-the-template)).

1. In your site repository on GitHub, create an environment named `production`. On a public repository this is free, with an approval gate. A private repository needs GitHub Pro or Team for environments, and Enterprise for required reviewers; see [GKE guide 11.1](./gke-cluster-setup.md#111-public-or-private).
2. Add `CLOUDFLARE_API_TOKEN` as an **environment secret** of `production`.
3. Add `CLOUDFLARE_ACCOUNT_ID` as a repository **variable**. It isn't a secret.

## 11. HSTS, once everything is HTTPS

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS stays staged by hand.

HSTS tells browsers never to use plain HTTP for your domain. It's powerful, and hard to undo once browsers have cached it, so raise it in stages.

In the chart's tunnel mode the backend's own HSTS is off by default, so this setting is the only source of it ([Security, HSTS](./security.md#hsts)). Turn stage 1 on before the site goes live.

1. Wait until every hostname you use serves valid HTTPS. With Cloudflare in front of everything, they all will.
2. Under **SSL/TLS → Edge Certificates → HSTS**, enable it with **Max-Age 1 day**. Leave **include subdomains** and **preload** off. Run the check below.
3. After a week with no problems, raise it to **6 months** and turn on **include subdomains**.
4. Only consider **preload** once the domain will be HTTPS forever. Removing a domain from browsers' preload lists takes months.

**Check** (run it on any live hostname, for example `preview.<domain>`):

```bash
curl -sS -I "https://preview.$DOMAIN" | grep -i '^strict-transport-security'
# → strict-transport-security: max-age=86400   (later: max-age=15552000; includeSubDomains)
```

## 12. Optional: a private R2 bucket for off-site backups

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. Optional, and not used by the template's CI: only a site repository would use it.

The template's chart backs Postgres up inside the cluster. A copy at a second provider protects you if the cluster, or the whole GCP project, is lost: the "3-2-1" rule. R2 charges nothing for egress, so restores cost nothing.

1. Under **R2 → Create bucket**, name it `platform-backups`, with a location hint close to the cluster, for example **Western Europe**.
2. Keep it **private**: no public access and no custom domain. Public media buckets often get one; backups never should.
3. Under the bucket's **Settings → Object lifecycle rules**, add a rule that deletes objects under the `pg/` prefix after **35 days**.
4. Under **R2 → Manage API tokens → Create Account API token**, choose **Object Read & Write**, applied to **`platform-backups` only**, with a TTL. Copy the **Access Key ID**, the **Secret Access Key** and the **S3 endpoint**, which looks like `https://<account-id>.r2.cloudflarestorage.com`.

**Check, as a round trip:**

```bash
source ~/cloudflare-setup.env
read -rs AWS_ACCESS_KEY_ID && export AWS_ACCESS_KEY_ID
read -rs AWS_SECRET_ACCESS_KEY && export AWS_SECRET_ACCESS_KEY
export AWS_DEFAULT_REGION=auto
EP="https://$CF_ACCOUNT_ID.r2.cloudflarestorage.com"

echo "backup check $(date -u +%FT%TZ)" > /tmp/r2-check.txt
aws s3 cp /tmp/r2-check.txt "s3://platform-backups/pg/r2-check.txt" --endpoint-url "$EP"
aws s3 ls "s3://platform-backups/pg/" --endpoint-url "$EP"           # → r2-check.txt is listed
aws s3 rm "s3://platform-backups/pg/r2-check.txt" --endpoint-url "$EP"
rm /tmp/r2-check.txt; unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

**Check that it is private.** R2's S3 endpoint refuses every unsigned request, public bucket or not, so an unsigned `curl` proves nothing. Public access goes only through the bucket's r2.dev URL or a custom domain. In the dashboard, **R2 → `platform-backups` → Settings** must show the **Public Development URL** disabled and no **Custom Domains**. Optionally, check it by API, with an account API token that has only **Workers R2 Storage: Read** and a short TTL:

```bash
read -rs CF_API_TOKEN && export CF_API_TOKEN
B="https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/r2/buckets/platform-backups"
curl -fsS "$B/domains/managed" -H "Authorization: Bearer $CF_API_TOKEN" | jq -r '.result.enabled'           # → false
curl -fsS "$B/domains/custom"  -H "Authorization: Bearer $CF_API_TOKEN" | jq -r '.result.domains | length'  # → 0
unset CF_API_TOKEN
```

Store the two keys as `production` environment secrets in whichever repository runs the backup job (optional, and not used by any template workflow). The pipeline work wires that up.

## 13. Final checklist and the values to hand over

> **Do now.**

| # | Check | Section | Expected |
|---|---|---|---|
| 1 | Two-factor sign-in, and you're the only member | 4 | yes |
| 2 | Cloudflare nameservers | 5 | `*.ns.cloudflare.com` |
| 3 | Full (strict), TLS 1.2 minimum, Always Use HTTPS | 6.1 and 8.5 | 301 redirect, TLS 1.2 accepted, TLS 1.1 refused by Cloudflare |
| 4 | DNSSEC active | 6.2 | DS present, `ad` flag |
| 5 | CAA, SPF and DMARC published | 6.3 | the three `dig` results |
| 6 | Deploy token active, with an expiry date | 7.2 | `active <date>` |
| 7 | Tunnel healthy from 2 Pods, no LoadBalancer Services | 8.3 | Registered connections, no resources found |
| 8 | Edge serves the test hostname correctly | 8.5 | all six checks |
| 9 | Access blocks anonymous visitors | 9 | 302 to `cloudflareaccess.com` |
| 10 | Token can really deploy | 10 | `<p>ok</p>`, then deleted |
| 11 | HSTS at stage 1 | 11 | `max-age=86400` |
| 12 | R2 is private and works, if you set it up | 12 | upload works; r2.dev off and no custom domain |

Then hand over these values. None of them is a secret:
- the domain;
- the account ID, which becomes the `CLOUDFLARE_ACCOUNT_ID` variable of your site repository;
- the tunnel name (`apps-dev`);
- your Zero Trust team name;
- the hostname plan from 2.2, if you changed it.

**Never send tokens or keys.** You put those into GitHub environment secrets yourself.

## 14. Costs

> **Do now.**

These are list prices from 2026; check Cloudflare's pricing pages.

| Item | Cost |
|---|---|
| Domain | at cost, roughly $10 to $20 a year depending on the TLD |
| DNS, edge TLS, WAF managed rules, DDoS protection | free plan |
| Cloudflare Tunnel | free |
| Cloudflare Access | free for up to 50 users |
| Workers static assets | static asset requests free; script requests free up to 100,000 a day |
| R2 | 10 GB-month of storage free, then about $0.015 per GB-month; egress free |

## 15. Troubleshooting

> **Do now.**

| Symptom | Likely cause | Fix |
|---|---|---|
| `dig NS` still shows the old registrar's nameservers | delegation not changed or not propagated | for a transferred domain, set Cloudflare's nameservers at the old registrar and wait up to 24 hours |
| No DS record after a day | DNSSEC not enabled, or the domain is at another registrar | for a domain registered elsewhere, enable it in Cloudflare, then paste the DS record into your registrar |
| Tunnel Pods `CreateContainerConfigError` | the `tunnel-token` Secret is missing or named differently | `kubectl -n cloudflared get secret tunnel-token` and recreate it as in 8.3 |
| Pods Running but the tunnel is **Down**, with `Unauthorized` in the logs | wrong or rotated tunnel token | copy the token again from the dashboard and recreate the Secret |
| Logs show connection timeouts on port 7844 | outbound traffic blocked | check Cloud NAT (GKE guide, section 6), and any egress NetworkPolicy in `cloudflared` |
| `hello.<domain>` returns **502 Bad Gateway** | the tunnel can't reach the Service | check that the URL in the public hostname matches `<svc>.<namespace>.svc.cluster.local:<port>`, that the Pod is Ready, that the target namespace carries the label `tier=app-ns` (8.3's connector policy blocks any other), and that no NetworkPolicy in it blocks the `cloudflared` namespace |
| **Error 1033** | no connector is online for that tunnel | `kubectl -n cloudflared get pods`; see the rows above |
| **Error 1016**, origin DNS error | the hostname was routed before the tunnel existed, or to a deleted tunnel | delete and re-add the public hostname |
| Too many redirects | the origin redirects to HTTPS while the tunnel speaks HTTP | point the route at the Service's HTTP port, and leave redirects to Cloudflare |
| Wrangler `compatibility_date` error | the date is in the future in UTC | use yesterday's UTC date, as in section 10 |
| Wrangler `403` / `Authentication error` | the token is missing a permission or the right zone | compare with 7.1; check the zone is selected under **Zone Resources** |
| The r2.dev URL or a custom domain is on (section 12's privacy check) | the bucket has public access | **R2 → bucket → Settings:** disable the Public Development URL and remove any custom domain |
| A preview's smoke test lands on a login page | the service token's policy is **Allow**, not **Service Auth** | change the `preview-smoke` policy's action to **Service Auth** (9.2) |
| A preview returns 502 | the internal Gateway's address changed, the connector's egress policy to it is missing, or the preview namespace lacks the `tier: preview-app` label | rerun [GKE 12.5](./gke-cluster-setup.md#125-optional-pull-request-previews)'s last step and update the route (9.2) |

## 16. What comes next

> **Planned changes.** Everything below is not implemented yet.

These come with the pipeline work:
- **Infrastructure as code:** the zone settings, DNS records, the tunnel with its routes, Access applications and the R2 bucket, all in OpenTofu or Terraform.
  - **Provider:** `cloudflare/cloudflare`, pinned to the v5 series. Its syntax differs from v4.
  - **State:** in a versioned, locked Google Cloud Storage bucket, never on a laptop or in Git.
  - **Changes:** reviewed as a plan in a pull request before anyone applies them.
- **App routes:** each environment's hostname becomes one DNS record and one Access application, and the tunnel routes for a host are the NOTES table's lines, all defined in code. (Already in the chart: in `edge.mode: tunnel` its NetworkPolicy admits the `cloudflared` namespace, and its NOTES print the route table, as in 8.4.)
- **Your site's pipeline:**
  - build, run the link and accessibility checks, then deploy with the pinned Wrangler and the environment-scoped token;
  - previews on `preview.<domain>`, behind Access;
  - a read-only verification step afterwards, with cold-start retries, a JSON report, and a non-zero exit code on failure.
- **Edge rules:** a rate-limiting rule for API hostnames, security headers set at the edge for the static site, and WAF exceptions where real traffic needs them.
- **Token rotation:** a scheduled workflow that warns 30 days before any token expires.
