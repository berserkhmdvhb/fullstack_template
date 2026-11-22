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
| You need | a Cloudflare account, the GKE cluster from the previous guide, and `curl`, `dig` and `openssl` |
| Written | 2026-11-20. Dashboard menus move around, so if a path has changed, search the dashboard for the setting's name. |

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
    └── app hostnames  ──────►  Cloudflare Tunnel ◄─── outbound only ─── cloudflared (2 Pods)
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

The template will support both. This guide sets up the Tunnel, because it's cheaper and exposes nothing.

## 2. Before you start

> **Do now.**

### 2.1 Tools

| Tool | Install | Check |
|---|---|---|
| curl, openssl | your package manager | `curl --version; openssl version` |
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
| `app-dev.<domain>`, `api-dev.<domain>` | the template app's dev environment, behind Access | when the pipeline deploys it |

Buying the domain now just reserves the name. Pointing the apex at a live site is the publish step.

## 3. Set your variables once

> **Do now.**

Identifiers such as the account ID and zone ID aren't secrets. Keep them out of committed files and docs anyway, because they make targeted mistakes and attacks easier. Tokens never go into this file at all.

```bash
cat > ~/cloudflare-setup.env <<'EOF'
export DOMAIN="your-domain.example"  # your registered domain
export CF_ACCOUNT_ID=""              # filled in section 5
export CF_ZONE_ID=""                 # filled in section 5
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
curl -s "https://rdap.org/domain/$DOMAIN" | python3 -c 'import json,sys; d=json.load(sys.stdin); print([e.get("vcardArray",[None,[]])[1][1][3] for e in d.get("entities",[]) if "registrar" in e.get("roles",[])])'
# → ['Cloudflare, Inc.']   (registry RDAP layouts vary, so the console's registration page is the fallback)
```

## 6. Zone baseline: TLS, DNSSEC and anti-spoofing

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS (section 11) stays a staged, manual rollout.

Make all of these settings in the zone.

### 6.1 TLS

| Setting | Where | Value | Why |
|---|---|---|---|
| Encryption mode | SSL/TLS → Overview | **Full (strict)** | Cloudflare always validates the origin's certificate, so there's no silent downgrade. Tunnel origins satisfy this automatically. |
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

> **Planned change.** Section 8.3's connector (the `cloudflared` namespace, Secret and Deployment) is not implemented yet. The chart's NetworkPolicy will let `edge.namespaceSelector` (default `cloudflared`) through, so the namespace label must match. 8.2 and 8.5 are **Do now**.

### 8.1 Create the tunnel

1. Open **Zero Trust**. The first time, pick a **team name** and the **Free** plan. It may ask for a payment method even on the free plan; you won't be charged.
2. Go to **Networks → Tunnels → Create a tunnel → Cloudflared**, and name it `apps-dev`.
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

### 8.4 Route a test hostname through the tunnel

```bash
kubectl create namespace edge-test
kubectl -n edge-test create deployment hello --image=nginxinc/nginx-unprivileged:stable-alpine --port=8080
kubectl -n edge-test expose deployment hello --port=8080
kubectl -n edge-test rollout status deployment/hello --timeout=180s
```

In **Zero Trust → Networks → Tunnels → apps-dev → Public Hostname → Add**, add the route below. Newer dashboards call this a *published application route*.

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

curl -sS -o /dev/null --tlsv1.1 --tls-max 1.1 "https://$H" && echo "TLS 1.1 ACCEPTED (wrong)" || echo "TLS 1.1 refused (right)"
# → TLS 1.1 refused (right)

echo | openssl s_client -connect "$H:443" -servername "$H" 2>/dev/null | openssl x509 -noout -issuer -enddate
# → an issuer such as Google Trust Services or Let's Encrypt, and a future end date
```

**Check:** all five outputs match the comments. Keep `hello` running for section 9.

## 9. Keep previews private with Cloudflare Access

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. Access apps and policies can be kept in code.

> **Planned change.** The `cloudflare-access` auth mode needs your Zero Trust team domain and the application's AUD tag; note both.

Cloudflare Access puts a login in front of a hostname. It's free for up to 50 users. Use it for every hostname that isn't public yet.

1. Go to **Zero Trust → Access → Applications → Add an application → Self-hosted**.
2. Name it `hello (test)`, with application domain `hello.<domain>`.
3. Add a policy named `owner` with action **Allow**, and an include rule of **Emails → your address**.
4. **Login method:** One-time PIN, which is on by default, or add GitHub as a login method under **Settings → Authentication**.
5. Save.

**Check:**

```bash
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://hello.$DOMAIN"
# → 302 https://<team>.cloudflareaccess.com/...    (anonymous visitors are sent to the login)
```

In a browser, open `https://hello.<domain>`, log in with the one-time PIN sent to your email, and you see the nginx page. That's the whole flow working.

Now create the real one: an Access application for `preview.<domain>`, with the same `owner` policy. Then remove the test:
1. delete the `hello (test)` Access application;
2. delete the `hello` public hostname in the tunnel;
3. delete the test namespace:

```bash
kubectl delete namespace edge-test
dig +short "hello.$DOMAIN"     # → nothing, once the record is gone
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

The Wrangler version is pinned exactly, here and in CI, so a new release can't change a deploy without you noticing.

If the deploy fails with `403` or `Authentication error`, the token lacks a permission. Compare it with 7.1. This is exactly the failure section 7's rule 3 is meant to catch before CI hits it.

### Put the token where CI can use it

> This is your **site repository's** setting, not the template's: no template workflow reads `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_ACCOUNT_ID` (see [Platform Setup](./platform-setup.md#used-by-a-site-repository-not-by-the-template)).

1. In your site repository on GitHub, create an environment named `production`. On a public repository this is free, with an approval gate. A private repository needs GitHub Pro or Team for environments, and Enterprise for required reviewers; see [GKE guide 11.1](./gke-cluster-setup.md#111-public-or-private).
2. Add `CLOUDFLARE_API_TOKEN` as an **environment secret** of `production`.
3. Add `CLOUDFLARE_ACCOUNT_ID` as a repository **variable**. It isn't a secret.

## 11. HSTS, once everything is HTTPS

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS stays staged by hand.

HSTS tells browsers never to use plain HTTP for your domain. It's powerful, and hard to undo once browsers have cached it, so raise it in stages.

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
curl -s -o /dev/null -w '%{http_code}\n' "$EP/platform-backups/pg/r2-check.txt"
# → 400, 401 or 403: an unsigned request is refused. A 200 here means the bucket is public, so fix it.
aws s3 rm "s3://platform-backups/pg/r2-check.txt" --endpoint-url "$EP"
rm /tmp/r2-check.txt; unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

Store the two keys as `production` environment secrets in whichever repository runs the backup job (optional, and not used by any template workflow). The pipeline work wires that up.

## 13. Final checklist and the values to hand over

> **Do now.**

| # | Check | Section | Expected |
|---|---|---|---|
| 1 | Two-factor sign-in, and you're the only member | 4 | yes |
| 2 | Cloudflare nameservers | 5 | `*.ns.cloudflare.com` |
| 3 | Full (strict), TLS 1.2 minimum, Always Use HTTPS | 6.1 and 8.5 | 301 redirect, TLS 1.1 refused |
| 4 | DNSSEC active | 6.2 | DS present, `ad` flag |
| 5 | CAA, SPF and DMARC published | 6.3 | the three `dig` results |
| 6 | Deploy token active, with an expiry date | 7.2 | `active <date>` |
| 7 | Tunnel healthy from 2 Pods, no LoadBalancer Services | 8.3 | Registered connections, no resources found |
| 8 | Edge serves the test hostname correctly | 8.5 | all five checks |
| 9 | Access blocks anonymous visitors | 9 | 302 to `cloudflareaccess.com` |
| 10 | Token can really deploy | 10 | `<p>ok</p>`, then deleted |
| 11 | HSTS at stage 1 | 11 | `max-age=86400` |
| 12 | R2 is private and works, if you set it up | 12 | upload ok, unsigned GET refused |

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
| `hello.<domain>` returns **502 Bad Gateway** | the tunnel can't reach the Service | check that the URL in the public hostname matches `<svc>.<namespace>.svc.cluster.local:<port>`, that the Pod is Ready, and that no NetworkPolicy in the target namespace blocks the `cloudflared` namespace |
| **Error 1033** | no connector is online for that tunnel | `kubectl -n cloudflared get pods`; see the rows above |
| **Error 1016**, origin DNS error | the hostname was routed before the tunnel existed, or to a deleted tunnel | delete and re-add the public hostname |
| Too many redirects | the origin redirects to HTTPS while the tunnel speaks HTTP | point the route at the Service's HTTP port, and leave redirects to Cloudflare |
| Wrangler `compatibility_date` error | the date is in the future in UTC | use yesterday's UTC date, as in section 10 |
| Wrangler `403` / `Authentication error` | the token is missing a permission or the right zone | compare with 7.1; check the zone is selected under **Zone Resources** |
| R2 unsigned GET returns **200** | the bucket has public access or a custom domain | **R2 → bucket → Settings:** disable public access and remove any custom domain |

## 16. What comes next

> **Planned changes.** Everything below is not implemented yet.

These come with the pipeline work:
- **Infrastructure as code:** the zone settings, DNS records, the tunnel with its routes, Access applications and the R2 bucket, all in OpenTofu or Terraform.
  - **Provider:** `cloudflare/cloudflare`, pinned to the v5 series. Its syntax differs from v4.
  - **State:** in a versioned, locked Google Cloud Storage bucket, never on a laptop or in Git.
  - **Changes:** reviewed as a plan in a pull request before anyone applies them.
- **App routes:** each environment's hostname becomes a tunnel route, defined in code. The template's NetworkPolicies allow ingress from the `cloudflared` namespace.
- **Your site's pipeline:**
  - build, run the link and accessibility checks, then deploy with the pinned Wrangler and the environment-scoped token;
  - previews on `preview.<domain>`, behind Access;
  - a read-only verification step afterwards, with cold-start retries, a JSON report, and a non-zero exit code on failure.
- **Edge rules:** a rate-limiting rule for API hostnames, security headers set at the edge for the static site, and WAF exceptions where real traffic needs them.
- **Token rotation:** a scheduled workflow that warns 30 days before any token expires.
