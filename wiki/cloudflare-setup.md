# Cloudflare setup: domains, edge and secure access

This guide puts domains in front of the platform with Cloudflare. It covers:
- choosing the domains and hostnames, and getting them into Cloudflare;
- a hardened zone, with TLS, DNSSEC, anti-spoofing records, a rate limit and alerts;
- **Cloudflare Access**, so nothing that is not public is ever reachable;
- a **Cloudflare Tunnel** from the cluster, so the cluster has no public entry point, with its token kept in Secret Manager;
- the service's own hostnames and routes;
- scoped, expiring API tokens for CI;
- an optional private **R2** bucket for off-site backups.

It follows [GKE Cluster Setup](./gke-cluster-setup.md). Finish that one first: section 8 here needs the cluster. [Platform Setup](./platform-setup.md) gives the overall order.

Every step ends with a **Check**, with the expected result in the text below it. Don't move on until it passes. Every code block pastes into zsh or bash as it is: no comments, no `echo`, and `${VAR}` braced. Run the blocks from the repository root.

| | |
|---|---|
| Time | 1.5 to 2.5 hours. DNSSEC and a transferred domain can take up to a day to go active, but only 5.1 waits on that. |
| Cost | the domains, about $10 to $20 a year each depending on the TLD, sold at cost. Everything else here is on Cloudflare's free tiers. |
| You need | a Cloudflare account, the GKE cluster from the previous guide, and `curl`, `dig`, `openssl`, `jq`, `kubectl` and `gcloud` |
| Written | 2026-11-27, checked against Cloudflare's docs that day. Dashboard menus move around, so if a path has changed, search the dashboard for the setting's name. |

## Contents

1. [What you will build](#1-what-you-will-build)
2. [Before you start](#2-before-you-start)
3. [Set your variables once](#3-set-your-variables-once)
4. [Secure the Cloudflare account](#4-secure-the-cloudflare-account)
5. [Put the domains into Cloudflare](#5-put-the-domains-into-cloudflare)
6. [Zone baseline: TLS, DNSSEC, anti-spoofing, rate limit](#6-zone-baseline-tls-dnssec-anti-spoofing-rate-limit)
7. [API tokens: scoped, expiring and tested](#7-api-tokens-scoped-expiring-and-tested)
8. [Cloudflare Tunnel into GKE](#8-cloudflare-tunnel-into-gke)
9. [Access, hostnames and routes for the service](#9-access-hostnames-and-routes-for-the-service)
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
    │  HTTPS only (TLS 1.2+), DNSSEC-signed DNS, WAF managed rules, Access for what is not public
    ▼
 Cloudflare edge  ─── DNS for <prod-domain> and <lab-domain>, certificates, cache, DDoS protection
    │
    ├── <prod-domain>, www           → your static site (Workers static assets)      [at publish]
    │
    └── service hostnames ───►  Cloudflare Tunnel "<cluster>" ◄── outbound only ── cloudflared (2 Pods)
          dev, qa, previews: <lab-domain>                                          in the cluster
          production: <prod-domain>                                                     │ in-cluster
                                                                                        ▼
                                                                                  app Services

 Off-site backups ──► R2 bucket (private, no public URL, lifecycle-expired)          [optional]
```

**Why this design:**
- **No public origin.** `cloudflared` dials out to Cloudflare, so the cluster has no load balancer, no public IP and no open inbound port to attack. The only way in is through Cloudflare.
- **One tunnel per cluster.** It is named after the cluster and serves every environment and every zone of your account that runs on that cluster. A production cluster in another project gets its own tunnel.
- **One home for DNS and the edge.** The domain, its certificates, the WAF and the access control are all in one place, under one audit log.
- **Private by default, and closed before opened.** Every host that is not public gets its Access application before its route exists, so a route can never be public by mistake.
- **Least privilege, the same as on GCP.** Every CI token can do one job, on one account or zone, and it expires.

The tunnel is **remotely managed**: its routes live in Cloudflare and the connector only holds a token. Cloudflare recommends this mode, and an infrastructure-as-code tool can import it later ([section 16](#16-what-comes-next)). Once it is under code, routes are no longer edited in the dashboard.

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
| curl, openssl | your package manager | `curl --version`, `openssl version` |
| jq | your package manager | `jq --version` |
| dig | `sudo apt install dnsutils` (Debian/Ubuntu), `bind-utils` (Fedora) | `dig -v` |
| gcloud, kubectl, with the GKE kubeconfig | from the GKE guide | `source "${GKE_SETUP_ENV}" && kubectl get nodes` |
| helm | `scripts/tools.sh install helm4` | `helm version --short` |
| gh, for 9.3 | <https://cli.github.com> | `gh --version` |
| Docker with buildx, for the digest lookup of 8.6 | <https://docs.docker.com/engine/install/> | `docker buildx version` |
| Node.js 26+, only for section 10 | <https://nodejs.org> or your version manager | `node --version` |
| AWS CLI v2, only for section 12 | <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html> | `aws --version` |

The blocks that read the cluster's variables use `GKE_SETUP_ENV`, the variables file of the GKE guide. Set it in each terminal session first: `export GKE_SETUP_ENV="${HOME}/gke-setup.env"` (or the file you chose there).

### 2.2 The hostname plan

Decide these now. The rest of the guide uses them.

**One production domain per independent project.** A project's production hosts live on its own domain, so one project's mistake, expiry or takedown never touches another's.

**One shared lab domain for everything that is not production.** Every dev, qa and preview host of every project goes under one non-production zone, the `<lab-domain>`:
- `<project>-dev.<lab-domain>` and `<project>-qa.<lab-domain>` for the environments;
- `<project>-pr-<N>.<lab-domain>` for pull request previews, so the lab domain is also your `PREVIEW_DOMAIN`.

Always **one label** under the zone. The free Universal SSL certificate covers `<zone>` and `*.<zone>`, never a second level such as `*.preview.<lab-domain>`. One wildcard Access application on the lab domain then protects all of it, and it fails closed: a host nobody thought about is still behind login. The service name in the host lets many projects share the lab domain.

| Hostname | Serves | Access | When |
|---|---|---|---|
| `<prod-domain>`, `www.<prod-domain>` | your public site | public | at publish |
| `hello.<lab-domain>` | a temporary test service for this guide | the lab wildcard | removed at the end of section 8 |
| `<SERVICE_NAME>-dev.<lab-domain>`, `<SERVICE_NAME>-qa.<lab-domain>` | the template app's dev and qa environments: one host each, the `edge.hosts.root` of each environment (the whole app on one origin; an optional `<SERVICE_NAME>-api[-<env>].<domain>` is a backend-only host, see [Infrastructure](./infrastructure.md#edge-hostnames)) | the lab wildcard | when the pipeline deploys it |
| `<SERVICE_NAME>.<prod-domain>` | the production environment | its own Access application until you launch | when the pipeline deploys it |
| `<SERVICE_NAME>-pr-<N>.<lab-domain>` | optional pull request previews | the previews' application (9.2) | only if you turn previews on ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)) |

The deploy composes the dev, qa and production host from the `EDGE_DOMAIN` environment variable (9.3), so no real domain is ever committed.

**Check the TLD.** Cloudflare Registrar sells a fixed list of TLDs: <https://www.cloudflare.com/tld-policies/>. For a TLD it does not sell, such as `.lu` or `.eu`, you register elsewhere and onboard the domain (5.2). A `.dev` or `.app` domain is on the browsers' HSTS preload list, so browsers use HTTPS only and the staged HSTS rollout of section 11 does not apply to it.

Each hostname is one DNS record, the tunnel routes that the chart's NOTES print for it (9.4), and, while it isn't public, one Access application.

## 3. Set your variables once

> **Do now.**

Identifiers such as the account ID, zone IDs and the tunnel ID aren't secrets. Keep them out of committed files and docs anyway, because they make targeted mistakes and attacks easier. Tokens never go into this file at all.

This file is also the **inventory** that a later infrastructure-as-code import needs. It stays on your machine, untracked, readable by you only, and every later step that creates something with an ID adds it here.

```bash
cat > ~/cloudflare-setup.env <<'EOF'
export DOMAIN="your-domain.example"
export LAB_DOMAIN="your-lab-domain.example"
export CF_ACCOUNT_ID=""
export CF_ZONE_ID=""
export CF_LAB_ZONE_ID=""
export TEAM_NAME=""
export TUNNEL_ID=""
export PREVIEW_DOMAIN=""
EOF
chmod 600 ~/cloudflare-setup.env
source ~/cloudflare-setup.env
```

`DOMAIN` is the production domain, `LAB_DOMAIN` the lab domain (2.2). Fill in the rest as the sections say. To record a value later, append it with `printf`:

```bash
printf 'export ACCESS_APP_LAB_ID="%s"\n' "the-id-from-the-dashboard" >> ~/cloudflare-setup.env
```

To use a token in a shell without it landing in your history or on screen, read it like this:

```bash
printf 'Paste the token, then press Enter (nothing is shown): '
read -rs CF_API_TOKEN && export CF_API_TOKEN
printf '\n'
```

Use it, then run `unset CF_API_TOKEN`.

**Check:** run `source ~/cloudflare-setup.env && printf '%s\n' "${DOMAIN}"`. It prints your production domain.

## 4. Secure the Cloudflare account

> **Do now.**

The account controls your DNS, so anyone who takes it over can redirect all your traffic.

1. **Turn on two-factor authentication** under **My Profile → Authentication**. Use a security key or passkey if you have one, otherwise an authenticator app.
2. **Save the backup codes** somewhere offline, such as a password manager or paper. Don't keep them in this repo or on this machine in plain text.
3. **Check who has access.** Under **Manage Account → Members** you should be the only member.
4. **Know where the audit log is:** **Manage Account → Audit Log**. Every change in this guide shows up there.

**Check:** sign out and back in. You're asked for the second factor.

## 5. Put the domains into Cloudflare

> **Do now.** Do this for the production domain and for the lab domain. They can be one purchase or two.

### 5.1 Register at Cloudflare, or onboard a domain from another registrar

**Cloudflare Registrar** (the TLD is on its list, 2.2):

1. Open **Domains → Register Domains** (older dashboards: **Domain Registration**) and search for a name. Cloudflare charges the registry's price, with no markup.
2. Buy it with **auto-renew on**, **WHOIS redaction** (on by default) and **transfer lock** (on by default).
3. Cloudflare creates the zone and uses its own nameservers automatically. Its registrar requires its own DNS.

**Another registrar** (a TLD Cloudflare does not sell, or a domain you already own):

1. Register the domain there, or use the one you have.
2. In Cloudflare, open **Domains → Onboard a domain**, enter the name and choose the **Free** plan. Cloudflare scans the existing DNS records: check them against what your registrar shows, and add the ones it missed.
3. **Turn DNSSEC off at the old registrar first** if it is on. If the old registrar keeps a DS record while the nameservers change, resolvers that validate DNSSEC fail the domain until it is removed.
4. At the registrar, replace the nameservers with the two Cloudflare shows you.
5. Wait for the zone to turn **Active**. It usually takes minutes and can take up to 24 hours.
6. After it is Active, enable DNSSEC in Cloudflare (6.2) and add the DS record it shows to the registrar. A domain that Cloudflare registered needs no DS step.

### 5.2 Verify the registrant's email

ICANN requires the registrant's email address to be verified after a registration, a transfer or a change of contact. The registrar sends a message with a link: open it within the time it states, usually 15 days. Otherwise the domain is put on hold and stops resolving. Check the inbox of the contact address (and its spam folder) today.

### 5.3 Record the IDs

Copy the **Zone ID** and **Account ID** of each zone from its **Overview** page, in the right-hand column, into `~/cloudflare-setup.env`: `CF_ZONE_ID` for the production domain, `CF_LAB_ZONE_ID` for the lab domain, `CF_ACCOUNT_ID` for the account.

**Check:**

```bash
source ~/cloudflare-setup.env
dig +short NS "${DOMAIN}"
dig +short NS "${LAB_DOMAIN}"
curl -fsSL "https://rdap.org/domain/${DOMAIN}" | jq -r '.entities[]? | select(.roles | index("registrar")) | .vcardArray[1][] | select(.[0] == "fn") | .[3]'
```

Each `dig` prints two names ending in `.ns.cloudflare.com`. The RDAP lookup prints `Cloudflare, Inc.` for a domain registered there (and the other registrar's name for an onboarded one); empty output means the registry's RDAP layout differs, and the dashboard's registration page is the fallback.

## 6. Zone baseline: TLS, DNSSEC, anti-spoofing, rate limit

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS (section 11) stays a staged, manual rollout.

Make all of these settings in **every** zone, the production domain's and the lab domain's.

### 6.1 TLS

| Setting | Where | Value | Why |
|---|---|---|---|
| Encryption mode | SSL/TLS → Overview → Configure → **Custom SSL/TLS** | **Full (strict)** | Cloudflare always validates the origin's certificate, so there's no silent downgrade on a Gateway origin. The mode does not govern the hop from Cloudflare to `cloudflared`: the tunnel encrypts that hop itself, whatever the mode ([Cloudflare's note on HTTPS origins](https://developers.cloudflare.com/tunnel/troubleshooting/https-origins/)). Keep Full (strict) anyway, so nothing downgrades if you later add another kind of origin. New zones start in **Automatic SSL/TLS**, where Cloudflare picks the mode. Custom pins it, so nothing changes it behind your back and the infrastructure code can state it. |
| Always Use HTTPS | SSL/TLS → Edge Certificates | **On** | Plain HTTP is redirected. |
| Minimum TLS version | SSL/TLS → Edge Certificates | **TLS 1.2** | Drops the broken protocol versions. |
| TLS 1.3 | SSL/TLS → Edge Certificates | **On** | Faster and safer. |
| Automatic HTTPS Rewrites | SSL/TLS → Edge Certificates | **On** | Fixes stray `http://` links. |
| HSTS | SSL/TLS → Edge Certificates | **leave off for now** | Turned on in [section 11](#11-hsts-once-everything-is-https). |

Wait until the zone's **Universal** certificate shows **Active** under **SSL/TLS → Edge Certificates** (usually minutes, up to a day for a new zone) before the end-to-end checks of 8.5. Until then browsers and `curl` see a certificate error.

### 6.2 DNSSEC

DNSSEC protects visitors from forged DNS answers. Under **DNS → Settings → DNSSEC**, click **Enable DNSSEC**. With Cloudflare as registrar, the DS record is published for you. With another registrar, copy the DS record Cloudflare shows into the registrar's DNS settings, after the zone is Active (5.1).

### 6.3 Records that stop spoofing and mis-issuance

Under **DNS → Records**, add these in each zone. This is for domains that send no mail. If you set up email forwarding later, see the note below the table.

| Type | Name | Content | Why |
|---|---|---|---|
| CAA | `@` | `0 issue "pki.goog"` | Lets Google issue certificates, which you'd need if you ever use the Gateway option. Cloudflare adds CAA entries for its own certificate authorities automatically. |
| CAA | `@` | `0 iodef "mailto:<you>"` | Where certificate authorities report refused requests. |
| MX | `@` | priority `0`, mail server `.` | A "null MX": the domain accepts no mail, so senders stop retrying. |
| TXT | `@` | `v=spf1 -all` | "This domain sends no email", so nobody can send mail as it. |
| TXT | `_dmarc` | `v=DMARC1; p=reject; sp=reject; adkim=s; aspf=s` | Receivers reject any mail that claims to come from it. |

If you turn on **Email Routing**, for example to forward `hello@<domain>` to your inbox, Cloudflare adds MX records and offers its own SPF record. Remove the null MX, accept its SPF value, `v=spf1 include:_spf.mx.cloudflare.net ~all`, in place of `-all`, and keep the DMARC record.

### 6.4 Security defaults

- **Security → WAF → Managed rules:** make sure the **Cloudflare Free Managed Ruleset** is on.
- **Bot Fight Mode:** leave it **off** for now. It challenges automated clients, and on the free plan you can't exempt your own health checks or API clients from it.

### 6.5 A rate limit on the production API

The free plan includes one rate-limiting rule. Spend it on the production API, the one path attackers hit hardest. Under **Security → Security rules → Create rule → Rate limiting rules** of the production zone:

| Field | Value |
|---|---|
| Name | `api-per-ip` |
| If incoming requests match | **URI Path** starts with `/api/` (on the free plan the expression fields are only **URI Path** and **Verified Bot**: there is no Hostname) |
| Characteristics | **IP** (the only choice on the free plan) |
| Requests, period | for example 100 requests per **10 seconds**, the only period the free plan has |
| Action, duration | **Block** for 10 seconds, the free plan's only duration |

The backend has its own per-client limit ([Security](./security.md#rate-limiting)); this one stops a flood before it reaches the tunnel. Because it cannot name a host, the rule applies to `/api` on every host of that zone, and the frontend's other paths are not covered. See [Cloudflare's rate limiting rules](https://developers.cloudflare.com/waf/rate-limiting-rules/).

**Check:**

```bash
source ~/cloudflare-setup.env
dig +short CAA "${DOMAIN}"
dig +short MX "${DOMAIN}"
dig +short TXT "${DOMAIN}"
dig +short TXT "_dmarc.${DOMAIN}"
dig +short DS "${DOMAIN}" @1.1.1.1
dig +dnssec "${DOMAIN}" SOA @1.1.1.1 | grep '^;; flags:'
```

The first prints your CAA lines plus Cloudflare's own, the second `0 .`, the third `"v=spf1 -all"` (or Cloudflare's SPF if you use Email Routing), the fourth `"v=DMARC1; p=reject; ..."`. The fifth prints a DS record: it can take a few hours, and up to a day, to appear. The last prints a flags line that includes `ad` once DNSSEC is active. Repeat with `${LAB_DOMAIN}`.

The TLS settings are checked against a real hostname in section 8.5, and the rate-limit rule is exercised once production serves traffic.

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
| `ci-site-deploy` | account | Account: **Workers Scripts: Edit**, **Account Settings: Read**. Zone `<prod-domain>`: **Workers Routes: Edit**, **DNS: Edit**, **Zone: Read** | your site's deploys (section 10 and its pipeline) |
| R2 backup credentials | account | **Object Read & Write** on the backup bucket only | the backup jobs (section 12) |
| `tf-cloudflare` (later) | account | Account: **Cloudflare Tunnel: Edit**, **Access: Apps and Policies: Edit**, **Access: Service Tokens: Edit**, **Access: Organizations, Identity Providers, and Groups: Edit**, **Workers R2 Storage: Edit**, **Notifications: Edit**, **Account Settings: Read**. Each zone: **Zone: Read**, **Zone Settings: Edit**, **DNS: Edit**, **SSL and Certificates: Edit**, **Zone WAF: Edit** (the token form has no separate DNSSEC permission) | the infrastructure-as-code step ([section 16](#16-what-comes-next)) |

The names are the ones in the token form's permission picker. If one differs in your dashboard, pick the closest and note it in the inventory.

The Tunnel token from section 8 isn't an API token. It's a connector secret that lives in Secret Manager and, from there, only in the cluster.

### 7.2 Create `ci-site-deploy`

1. Go to **Manage Account → Account API Tokens → Create Token → Custom token**. Account-owned tokens keep working if your user changes.
2. Add the permissions from 7.1. Under **Account Resources**, choose your account; under **Zone Resources**, choose **Specific zone → `<prod-domain>`**.
3. Set the **TTL** end date to 12 months from today.
4. Create it, then copy the token once. Cloudflare never shows it again.

**Check that it's active:**

```bash
source ~/cloudflare-setup.env
printf 'Paste the token, then press Enter (nothing is shown): '
read -rs CF_API_TOKEN && export CF_API_TOKEN
printf '\n'
curl -fsS "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/tokens/verify" -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result | "\(.status) \(.expires_on)"'
```

It prints `active` and the end date you set. Keep `CF_API_TOKEN` set for section 10, where the token is proved against the deploy API itself.

## 8. Cloudflare Tunnel into GKE

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. A tunnel made in the dashboard is imported by its ID, so record the ID (8.2).

The connector's manifests are files in [`deploy/cloudflared/`](../deploy/cloudflared/README.md), applied with `kubectl apply -f`. The chart's NetworkPolicy, in `edge.mode: tunnel`, admits the namespace `edge.namespaceSelector` selects (empty means `kubernetes.io/metadata.name: cloudflared`), so the namespace is named `cloudflared`.

### 8.1 The Zero Trust organisation, the login method and the `owner` policy

1. Open **Zero Trust**. The first time, pick a **team name** and the **Free** plan. It may ask for a payment method even on the free plan; you won't be charged. Put the team name in `~/cloudflare-setup.env` as `TEAM_NAME`. Your team domain is `<team>.cloudflareaccess.com`.
2. **Login method.** A new Zero Trust organisation starts with the **Cloudflare identity provider**, so you sign in with your Cloudflare account. One-time PIN is no longer added for you. To use One-time PIN or GitHub instead, add it under **Zero Trust → Integrations → Identity providers**.
3. **The `owner` policy,** reusable by every Access application. Go to **Zero Trust → Access controls → Policies → Add a policy**: name `owner`, action **Allow**, include rule **Emails → your address**. Record its ID (in the policy's URL), replacing the text in quotes:

```bash
printf 'export OWNER_POLICY_ID="%s"\n' "paste-the-policy-id" >> ~/cloudflare-setup.env
```

**Check:** the policy `owner` is listed under **Access controls → Policies**, and the last line of `~/cloudflare-setup.env` holds its ID.

### 8.2 Create the tunnel

1. Go to **Networking → Tunnels → Create a tunnel → Cloudflared**.
2. Name it **after the cluster** (the `CLUSTER` of the file `GKE_SETUP_ENV` names). One tunnel per cluster serves every environment and every zone of the account that runs on it. A production cluster in another project gets its own tunnel, named after that cluster.
3. On the install screen, copy the **token**: the long string after `--token` in any of the install commands. Don't run those commands, and don't paste the token anywhere but the prompt of 8.3. The cluster runs the connector instead.
4. Put the **tunnel ID** (on the tunnel's page) into `~/cloudflare-setup.env` as `TUNNEL_ID`.
5. Don't add routes yet. Section 8.4 and section 9 add them, each after its Access application.

**Check:** the tunnel is listed, with status **Inactive** until a connector runs.

### 8.3 Run the connector in the cluster

The connector runs as 2 replicas spread over the nodes, so one node going down doesn't cut the tunnel. The connector spreads its replicas across nodes and, on a regional cluster, across zones (best effort), so a production cluster in two zones keeps a connector in each zone. It uses the `restricted` Pod Security profile, a read-only filesystem, no Kubernetes API token, a disruption budget and a PriorityClass that keeps it ahead of the app Pods on spot nodes. Its image is pinned by digest in [`deployment.yaml`](../deploy/cloudflared/deployment.yaml).

The token never becomes a hand-made Kubernetes Secret. It lives in **Secret Manager** as `cloudflared-tunnel-token`, and the External Secrets Operator (installed by the GKE guide) syncs it into the Secret `tunnel-token` through the namespace's own `secret-manager` SecretStore.

**The Secret Manager secret and its grant** come from your platform's infrastructure-as-code if you have it: a secret named `cloudflared-tunnel-token`, and a conditional Secret Accessor grant limited to names with the prefix `cloudflared-` for the principal of the `cloudflared` namespace's `external-secrets` service account. Without infrastructure-as-code, this is the gcloud equivalent, the same pattern as [GKE 14.2](./gke-cluster-setup.md):

```bash
source "${GKE_SETUP_ENV}"
gcloud services enable secretmanager.googleapis.com
gcloud secrets create cloudflared-tunnel-token --replication-policy=user-managed --locations="${REGION}"
pool="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${PROJECT_ID}.svc.id.goog"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --role=roles/secretmanager.secretAccessor --member="principal://iam.googleapis.com/${pool}/subject/ns/cloudflared/sa/external-secrets" --condition="expression=resource.name.startsWith(\"projects/${PROJECT_NUMBER}/secrets/cloudflared-\"),title=eso-cloudflared" --format=none
```

The first command prints nothing visible but a confirmation, and the last one prints nothing.

**Store the token** as a new version, from standard input, so it is never in a file, an argument or the history:

```bash
printf 'Paste the tunnel token, then press Enter (nothing is shown): '
read -rs TUNNEL_TOKEN
printf '\n'
printf '%s' "${TUNNEL_TOKEN}" | gcloud secrets versions add cloudflared-tunnel-token --data-file=-
unset TUNNEL_TOKEN
```

It prints `Created version [1] of the secret [cloudflared-tunnel-token].`

**Apply the namespace, the store and the ExternalSecret,** then wait for the Secret:

```bash
source "${GKE_SETUP_ENV}"
kubectl apply -f deploy/cloudflared/namespace.yaml
kubectl apply -f deploy/cloudflared/priorityclass.yaml
kubectl -n cloudflared apply -f deploy/external-secrets/secretstore-secret-manager.yaml
kubectl apply -f deploy/cloudflared/externalsecret.yaml
kubectl -n cloudflared wait --for=condition=Ready externalsecret/tunnel-token --timeout=120s
kubectl -n cloudflared get secret tunnel-token
```

The wait prints `externalsecret.external-secrets.io/tunnel-token condition met`, and the last command lists the Secret `tunnel-token` with 1 data item. If the wait times out, run `kubectl -n cloudflared describe externalsecret tunnel-token`: a permission error means the grant above is missing or names another namespace.

**Run the connector:**

```bash
kubectl apply -f deploy/cloudflared/deployment.yaml -f deploy/cloudflared/pdb.yaml
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
```

**Check:**

```bash
kubectl -n cloudflared get pods -o wide
kubectl -n cloudflared logs deployment/cloudflared | grep -m 4 'Registered tunnel connection'
kubectl get svc -A --field-selector spec.type=LoadBalancer
```

The first shows 2 Pods Running and Ready, ideally on different nodes. The second prints up to 4 lines, one per connection to a nearby Cloudflare data centre. The third prints `No resources found`: the cluster has no public entry point. In the dashboard the tunnel shows **Healthy**. Warnings about ICMP or UDP buffer sizes in the logs are harmless.

Now close the connector's network. Nothing may connect to it, and it may reach only DNS, the Cloudflare edge and the app namespaces:

```bash
kubectl apply -f deploy/cloudflared/networkpolicy.yaml
```

- Port 7844 is the tunnel: QUIC over UDP, or HTTP/2 over TCP.
- Port 443 is for cloudflared's optional features.
- The last rule is the app namespaces. The bootstrap labels them `tier: app-ns`, and previews `tier: preview-app`. Label any other namespace the tunnel serves `tier=app-ns`, or add it to the policy.

**Check:**

```bash
kubectl -n cloudflared rollout restart deployment/cloudflared
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
kubectl -n cloudflared logs deployment/cloudflared --since=3m | grep -c 'Registered tunnel connection'
POD_IP="$(kubectl -n cloudflared get pod -l app=cloudflared -o jsonpath='{.items[0].status.podIP}')"
kubectl -n default run np-probe --rm -i --restart=Never --image=busybox:1.37 -- wget -qO- -T 5 "http://${POD_IP}:2000/ready"
printf 'exit=%s\n' "$?"
```

The count is 1 or more: the connector still reaches Cloudflare. The probe times out and `exit=` is non-zero: nothing in the cluster can reach the connector.

**Rotating the token.** The operator refreshes the Secret only every hour (`refreshInterval: 1h`), and the token reaches the Pod as an environment variable, so a running Pod never sees a new value. Restarting before the Secret has synced starts new Pods with the old token, and the rollout stalls on `Unauthorized`. So: add the new version, force a sync, wait for it, then restart.

```bash
printf 'Paste the new tunnel token, then press Enter (nothing is shown): '
read -rs TUNNEL_TOKEN
printf '\n'
printf '%s' "${TUNNEL_TOKEN}" | gcloud secrets versions add cloudflared-tunnel-token --data-file=-
unset TUNNEL_TOKEN
OLD="$(kubectl -n cloudflared get externalsecret tunnel-token -o jsonpath='{.status.refreshTime}')"
kubectl -n cloudflared annotate externalsecret tunnel-token force-sync="$(date +%s)" --overwrite
for i in $(seq 1 30); do NEW="$(kubectl -n cloudflared get externalsecret tunnel-token -o jsonpath='{.status.refreshTime}')"; [ "${NEW}" != "${OLD}" ] && break; sleep 2; done
printf 'before %s, after %s\n' "${OLD}" "${NEW}"
kubectl -n cloudflared rollout restart deployment/cloudflared
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=120s
```

The annotate command prints `externalsecret.external-secrets.io/tunnel-token annotated`. The loop polls the ExternalSecret's `.status.refreshTime`, the time the operator last fetched the secret, for up to 60 seconds, and the `printf` shows two different timestamps, for example `before 2026-11-27T10:00:00Z, after 2026-11-27T10:03:12Z`: the new token is in the Secret. The rollout ends with `deployment "cloudflared" successfully rolled out`, and the tunnel shows **Healthy**. If the two timestamps are equal after 60 seconds, the sync did not happen: do not rely on the restart. Run `kubectl -n cloudflared describe externalsecret tunnel-token`, read the status message and events (a permission error means the grant of 8.3 is wrong), fix it, and repeat from the annotate line.

### 8.4 A test hostname, behind Access first

> The wildcard Access application created here stays: it protects every host under the lab domain from now on.

Deploy a throwaway service:

```bash
kubectl create namespace edge-test
kubectl label namespace edge-test tier=app-ns
kubectl -n edge-test create deployment hello --image=nginxinc/nginx-unprivileged:stable-alpine --port=8080
kubectl -n edge-test expose deployment hello --port=8080
kubectl -n edge-test rollout status deployment/hello --timeout=180s
```

**1. The Access application, before the route.** Go to **Zero Trust → Access controls → Applications → Create new application → Self-hosted and private → Add public hostname**:

| Field | Value |
|---|---|
| Application name | `lab` |
| Subdomain | `*` |
| Domain | `<lab-domain>` |
| Policy | `owner` (Allow), the reusable policy of 8.1 |

Save it, and record its ID (in the application's URL), replacing the text in quotes:

```bash
printf 'export ACCESS_APP_LAB_ID="%s"\n' "paste-the-application-id" >> ~/cloudflare-setup.env
```

Access applications are deny by default: this one protects every host one label under the lab domain, dev, qa, previews and anything else, and any host nobody has thought about, because no route or DNS record can make a hostname public that this wildcard covers. Don't create the wildcard on the production domain: it would cover `www` and the site too.

**2. The route.** Open the tunnel (**Networking → Tunnels → your tunnel**), **Routes** tab, **Add route → Published application**:

| Field | Value |
|---|---|
| Subdomain | `hello` |
| Domain | `<lab-domain>` |
| Path | empty |
| Service URL | `http://hello.edge-test.svc.cluster.local:8080` |

The Service URL is a single field with the scheme: `http://<service>.<namespace>.svc.cluster.local:<port>`. Cloudflare creates the proxied DNS record for you.

**Check:**

```bash
source ~/cloudflare-setup.env
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://hello.${LAB_DOMAIN}"
```

It prints `302` and a URL under `https://<team>.cloudflareaccess.com/`: anonymous visitors are sent to the login. In a browser, open `https://hello.<lab-domain>`, log in with the method you set up, and you see the nginx page. That's the whole flow working. A request that is not logged in never reached the Pod.

### 8.5 Check the edge, end to end

Wait for the Universal certificate to be **Active** (6.1). The hostname is behind Access, so the responses below are Access's, which is what proves the edge settings.

```bash
source ~/cloudflare-setup.env
H="hello.${LAB_DOMAIN}"
dig +short "${H}"
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "http://${H}"
curl -sS -I "https://${H}" | grep -iE '^(HTTP/|server:)'
curl -sS -o /dev/null -w '%{http_code}\n' --tlsv1.2 --tls-max 1.2 "https://${H}"
out="$(openssl s_client -connect "${H}:443" -servername "${H}" -tls1_1 -cipher 'DEFAULT:@SECLEVEL=0' </dev/null 2>&1)"
case "${out}" in
  *"alert protocol version"*|*"alert handshake failure"*) printf 'TLS 1.1 refused by Cloudflare (right)\n' ;;
  *"New, TLSv1.1, Cipher is"*) printf 'TLS 1.1 ACCEPTED (wrong)\n' ;;
  *) printf 'inconclusive: your OpenSSL could not offer TLS 1.1; check the setting in the dashboard\n' ;;
esac
openssl s_client -connect "${H}:443" -servername "${H}" </dev/null 2>/dev/null | openssl x509 -noout -issuer -enddate
```

Expected, in order:
1. Cloudflare edge IPs (such as `104.x.x.x` or `172.6x.x.x`), never a Google IP.
2. `301 https://hello.<lab-domain>/`: Always Use HTTPS.
3. `HTTP/2 302` and `server: cloudflare`.
4. `302`: TLS 1.2 is accepted.
5. `TLS 1.1 refused by Cloudflare (right)`. Modern OpenSSL won't offer TLS 1.1 at its default security level, so the command lowers it for this one test.
6. An issuer such as Google Trust Services or Let's Encrypt, and a future end date.

**Check:** all six match. Then remove the test:
1. delete the `hello` route in the tunnel (keep the `lab` Access application);
2. delete the DNS record, if the route's deletion left it: under **DNS → Records** of the lab zone, delete the CNAME `hello` that points at `<tunnel-id>.cfargotunnel.com`;
3. delete the test namespace.

```bash
kubectl delete namespace edge-test
dig +short "hello.${LAB_DOMAIN}"
```

The `dig` prints nothing once the record is gone. A deleted route does not always delete its DNS record, so while `dig` still answers, delete the CNAME by hand.

### 8.6 Keep the connector current

> **Do now.**

Cloudflare supports each cloudflared release for one year from its release date, so check <https://github.com/cloudflare/cloudflared/releases> monthly. To upgrade, pick the release, find its digest, and change the tag and the digest of [`deployment.yaml`](../deploy/cloudflared/deployment.yaml) together (Renovate proposes it too):

```bash
CLOUDFLARED_VERSION="2026.11.2"
docker buildx imagetools inspect "cloudflare/cloudflared:${CLOUDFLARED_VERSION}" --format '{{.Manifest.Digest}}'
```

The second line prints `sha256:<64 hex>`: the digest of the index. Edit the `image:` line of the manifest to `cloudflare/cloudflared:<version>@<digest>`, then apply it:

```bash
kubectl apply -f deploy/cloudflared/deployment.yaml
kubectl -n cloudflared rollout status deployment/cloudflared --timeout=180s
```

The two replicas and the disruption budget keep the tunnel up while it rolls. Commit the change: the manifest records the version in use.

**Check:** `kubectl -n cloudflared get deployment cloudflared -o jsonpath='{.spec.template.spec.containers[0].image}'` prints the new image, and the tunnel shows **Healthy** in the dashboard.

### 8.7 Get warned: notifications

> **Do now.**

Two failures are silent otherwise: the tunnel going down, and an Access service token expiring. Under **Manage Account → Notifications → Add**, create:
- **Tunnel Health Alert**, for your tunnel, to your email;
- **Expiring Access Service Token Alert**, to your email (9.2 creates the token it watches). Cloudflare may offer this one only with a paid Access plan; if it is missing from your list, keep a calendar reminder a week before the token expires instead.

See [Cloudflare's tunnel notifications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/monitor-tunnels/notifications/).

## 9. Access, hostnames and routes for the service

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. Access applications and policies can be kept in code, and so can the routes.

> **Note both values.** The backend's `AUTH_MODE=cloudflare-access` needs your Zero Trust team domain and the application's AUD tag (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`: [Environment Variables](./environment-variables.md), [Security](./security.md#authentication--authorization)). The team domain is `<team>.cloudflareaccess.com`, with the team name you picked in 8.1. The AUD tag is on the application: newer dashboards show it on its **Additional settings** tab, older ones on its **Overview** tab. The route's **Protect with Access** option (under the route's additional application settings) makes `cloudflared` validate Cloudflare's Access token itself, so a request that bypassed Access is refused at the connector; turn it on for routes that are not public if you want that second layer.

### 9.1 Production stays behind Access until launch

Dev and qa are covered by the lab wildcard (8.4). Production lives on the production domain, which has no wildcard, so give its host its own application **before** its route exists. **Zero Trust → Access controls → Applications → Create new application → Self-hosted and private → Add public hostname**: name `<service>-production`, subdomain `<SERVICE_NAME>`, domain `<prod-domain>`, policy `owner`. At launch, delete this application and only this one (9.5). Record its ID, replacing the text in quotes:

```bash
printf 'export ACCESS_APP_PROD_ID="%s"\n' "paste-the-application-id" >> ~/cloudflare-setup.env
```

**Which tunnel carries production.** A tunnel belongs to one cluster. If production runs on its own cluster, in its own project, repeat 8.2, 8.3 and 8.7 there: that project's Secret Manager (its own `cloudflared-tunnel-token` secret and grant), and a tunnel named after that cluster, with its own connector. Production's routes (9.4) go into that tunnel, and the dev and qa routes into the non-production cluster's tunnel. The Zero Trust organisation, the `owner` policy and the Access applications are account-wide and are not repeated. With a single cluster, one tunnel carries every environment.

**Check:**

```bash
source ~/cloudflare-setup.env
printf 'Hostname to check: '
read -r HOST_TO_CHECK
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://${HOST_TO_CHECK}"
```

Answering with a protected hostname that has a route prints `302` and a `cloudflareaccess.com` URL. Before its route exists, the host has no DNS record and the request fails: that is fine, the application only has to exist first.

### 9.2 Optional: the previews' hostnames, Access application and service token

> **Only if you turn previews on** with public hostnames ([CI/CD, Pull request previews](./ci-cd.md#pull-request-previews)), after [GKE 12.5](./gke-cluster-setup.md#125-optional-pull-request-previews).

Previews live on the lab domain, which is `PREVIEW_DOMAIN` (put it in `~/cloudflare-setup.env` and in the `preview` environment of GitHub, [Platform Setup](./platform-setup.md#variables-and-secrets)). Each preview has one hostname, one label under it, which the free Universal SSL certificate covers (2.2).

1. **The service token.** Go to **Zero Trust → Access controls → Service credentials → Service Tokens → Create Service Token**, named `preview-smoke`, with a duration of one year and a renewal reminder (section 7's rule 2; the expiring-token notification of 8.7 also warns you, where your plan has it). Copy the **Client ID** and **Client Secret**; the secret is shown once. In GitHub, add them as the secrets `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` of the `preview` environment. Record the token's ID (shown in the token's row or URL), replacing the text in quotes:

   ```bash
   printf 'export PREVIEW_SMOKE_TOKEN_ID="%s"\n' "paste-the-token-id" >> ~/cloudflare-setup.env
   ```

2. **The Access application,** before the route, so no preview is ever public. **Create new application → Self-hosted and private → Add public hostname**, named `previews`, with subdomain `<SERVICE_NAME>-pr-*` and domain `<lab-domain>` (one wildcard inside the label: `*` stands for the pull request number). Add two policies: `owner` (**Allow**, as in 8.1) and `preview-smoke` (action **Service Auth**, include **Service Token → `preview-smoke`**). Service Auth matters: with **Allow**, Access sends the smoke test to a login page. This application is more specific than the lab wildcard, so Cloudflare applies it to the previews only, and the token cannot open dev or qa. Record its ID as `ACCESS_APP_PREVIEWS_ID`:

   ```bash
   printf 'export ACCESS_APP_PREVIEWS_ID="%s"\n' "paste-the-application-id" >> ~/cloudflare-setup.env
   ```

3. **The route.** In the tunnel's **Routes**, add subdomain `*`, domain `<lab-domain>`, path empty, Service URL `http://<PREVIEW_GW_IP>:80` (GKE 12.5). A wildcard route is the catch-all of its host pattern, so keep it **last** in the list: routes match in order, and a host added later (9.4) is appended after it and would be swallowed. If the dashboard doesn't create the DNS record, add a proxied `CNAME` `*` → `<TUNNEL_ID>.cfargotunnel.com`.

**Check,** once a preview is deployed. The first request is anonymous, the second sends the token, and the third sends the token to a dev host, which must be refused:

```bash
source ~/cloudflare-setup.env
printf 'Preview hostname (for example your-service-name-pr-1.%s): ' "${PREVIEW_DOMAIN}"
read -r P
printf 'Dev hostname: '
read -r D
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://${P}"
printf 'Client ID: '
read -rs CF_ACCESS_CLIENT_ID
printf '\nClient secret: '
read -rs CF_ACCESS_CLIENT_SECRET
printf '\n'
curl -sS -o /dev/null -w '%{http_code}\n' -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" "https://${P}"
curl -sS -o /dev/null -w '%{http_code}\n' -H "CF-Access-Client-Id: ${CF_ACCESS_CLIENT_ID}" -H "CF-Access-Client-Secret: ${CF_ACCESS_CLIENT_SECRET}" "https://${D}"
unset CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET
```

The first prints `302` and a `cloudflareaccess.com` URL: anonymous visitors go to the login. The second prints `200`: the service token passes. The third must **not** print `200`: it prints `302` or `403`. The deploy sends the token from a private header file, never on a command line; this check puts it on `curl`'s command line once, in your own shell, which is the one place it is acceptable.

### 9.3 `EDGE_DOMAIN` and the app's settings

> **Do now.**

**The hostname comes from a GitHub variable, not from the repository.** The committed `values.<env>.yaml` keep their placeholder host, so no real domain is ever committed, and the deploy composes the real one from the environment variable `EDGE_DOMAIN`, the way `PREVIEW_DOMAIN` composes a preview's. With it set, the deploy passes one more values file after the committed one, and the placeholder guard has nothing left to refuse.

| GitHub environment | `EDGE_DOMAIN` | The host the deploy composes |
|---|---|---|
| `dev` | `<lab-domain>` | `<SERVICE_NAME>-dev.<lab-domain>` |
| `qa` | `<lab-domain>` | `<SERVICE_NAME>-qa.<lab-domain>` |
| `production` | `<prod-domain>` | `<SERVICE_NAME>.<prod-domain>` |

Set it once per environment (and in each `drift-<env>` environment, if you turned the live-drift check on, so its render matches what was deployed). Pass the repository explicitly:

```bash
source ~/cloudflare-setup.env
source "${GKE_SETUP_ENV}"
gh variable set EDGE_DOMAIN --env dev --body "${LAB_DOMAIN}" -R "${GH_REPO}"
gh variable set EDGE_DOMAIN --env qa --body "${LAB_DOMAIN}" -R "${GH_REPO}"
gh variable set EDGE_DOMAIN --env production --body "${DOMAIN}" -R "${GH_REPO}"
```

Each command prints a confirmation line. The settings script can set the same variables from its vars file, as `ENV_dev_EDGE_DOMAIN` and so on ([GitHub repository settings](./github-repository-settings.md)). An optional backend-only host (`edge.hosts.api`) is not composed: leave it empty.

**The app's settings,** in each `values.<env>.yaml` (no secrets and no domains):

```yaml
edge:
  mode: tunnel
config:
  TRUSTED_PROXY_CIDRS: "<the cluster's Pod range>"
  TRUST_CF_CONNECTING_IP: "true"
```

- `edge.mode: tunnel` renders no Ingress or HTTPRoute, and the chart's NetworkPolicy admits the `cloudflared` namespace.
- `TRUSTED_PROXY_CIDRS` is the cluster's Pod range, the source of the connector's traffic. Trusting a range trusts every Pod in it ([Security, Client IP](./security.md#client-ip)). Print it:

```bash
source "${GKE_SETUP_ENV}"
gcloud container clusters describe "${CLUSTER}" --location="${ZONE}" --format='value(clusterIpv4Cidr)'
```

- `TRUST_CF_CONNECTING_IP: "true"` only because `cloudflared` is the only way in.
- Set the `DEPLOY_URL` variable of each environment to `https://` and its host, so GitHub links the deployment ([Platform Setup](./platform-setup.md#variables-and-secrets)).
- The dev, qa and production smoke tests run through a port-forward, so they need no Access token. Only previews do, because they test through the public URL (9.2).

### 9.4 Route the service through the tunnel

The chart prints the routes: in `edge.mode: tunnel` its NOTES hold the route table, built from the one routes list (`edge.routes`, [Infrastructure, Edge](./infrastructure.md#the-routes-list)). Print it before the first deploy with a client-side dry run, using the environment's values and its host. Set the variables, then run the command for each environment:

```bash
source ~/cloudflare-setup.env
SERVICE_NAME="your-service-name"
ENVIRONMENT="dev"
EDGE_HOST="${SERVICE_NAME}-${ENVIRONMENT}.${LAB_DOMAIN}"
.tools/bin/helm install "${SERVICE_NAME}" charts/your-service-name --dry-run=client --namespace "${SERVICE_NAME}-${ENVIRONMENT}" -f "charts/your-service-name/values.${ENVIRONMENT}.yaml" --set edge.mode=tunnel --set "edge.hosts.root=${EDGE_HOST}" | sed -n '/^NOTES:/,$p'
```

For production, set `ENVIRONMENT="production"` and `EDGE_HOST="${SERVICE_NAME}.${DOMAIN}"`. After a deploy, `helm get notes <release> -n <namespace>` prints the same table.

For the default values, the NOTES for dev read as follows (the ports are the chart's defaults, `config.BACKEND_PORT` and `config.FRONTEND_PORT`; always copy yours from NOTES):

```text
your-service-name-dev.<lab-domain>   ^/openapi\.json$    http://your-service-name-backend.your-service-name-dev.svc.cluster.local:8000
your-service-name-dev.<lab-domain>   ^/docs(/|$)         http://your-service-name-backend.your-service-name-dev.svc.cluster.local:8000
your-service-name-dev.<lab-domain>   ^/api(/|$)          http://your-service-name-backend.your-service-name-dev.svc.cluster.local:8000
your-service-name-dev.<lab-domain>   (empty)             http://your-service-name-frontend.your-service-name-dev.svc.cluster.local:3000
```

**Why the paths look like that.** `cloudflared` turns each published route into an ingress rule whose **path is an unanchored regular expression**, matched against the request path, and the **first matching rule wins**. A bare `/api` would also match `/apix` and `/x/api`. So the chart anchors each route the way Cloudflare matches it:

| `edge.routes` entry | Path to enter | Matches |
|---|---|---|
| `PathPrefix` `/api` | `^/api(/\|$)` | `/api`, `/api/v1/health`; not `/apix` |
| `Exact` `/openapi.json` | `^/openapi\.json$` | exactly that path |
| `PathPrefix` `/` | empty | everything else: the catch-all |

The pipe inside the table cell is escaped for Markdown only: enter `^/api(/|$)`.

**The route table for each environment.** One host per environment, four routes each, added to the tunnel of the cluster that runs that environment (with one cluster, the same tunnel for all three; with a separate production cluster, production's go into its own tunnel, 9.1):

| Environment | Host | Routes, in this order |
|---|---|---|
| dev | `<SERVICE_NAME>-dev.<lab-domain>` | `^/openapi\.json$`, `^/docs(/\|$)`, `^/api(/\|$)` to the backend; empty to the frontend |
| qa | `<SERVICE_NAME>-qa.<lab-domain>` | the same four, in the namespace `<SERVICE_NAME>-qa` |
| production | `<SERVICE_NAME>.<prod-domain>` | the same four, in the namespace `<SERVICE_NAME>-production` |

The Service URL of each is `http://<release>-backend.<namespace>.svc.cluster.local:<port>` or `http://<release>-frontend.<namespace>.svc.cluster.local:<port>`; with the WAF on (`waf.enabled`), the backend routes point at the WAF Service instead, and NOTES prints that.

**Add them,** after the host's Access application exists (8.4 for dev and qa, 9.1 for production). In the tunnel's **Routes** tab, **Add route → Published application**, once per line of NOTES, in NOTES' order and the catch-all last:

| Field | Value |
|---|---|
| Subdomain, Domain | the host of the line, for example `your-service-name-dev` and `<lab-domain>` |
| Path | the second column of NOTES, as is; empty for `(empty)` |
| Service URL | the third column, as is, `http://...` |

Cloudflare creates the host's proxied DNS record with the first route. The path is not stripped: the Service receives the full request path. If the list shows the routes in another order than NOTES, delete the out-of-order ones and add them again in order, so the catch-all is last.

**Order matters across hosts too.** The dashboard appends every new route at the end of the list. The previews' wildcard route of 9.2 (`*.<lab-domain>`) matches every host of the lab domain, so any host you add after it, with its routes, lands behind it and is swallowed: its traffic goes to the previews' Gateway. After adding each host, open the **Routes** list and check that the wildcard is still last. If it is not, delete the wildcard route and add it again (the dashboard has no reordering you can rely on), then re-check. The same holds for the catch-all of each host against that host's own routes.

**Check** (the host is behind Access, so use a browser logged in as yourself): `https://<host>/api/v1/healthz` shows the backend's liveness answer, `https://<host>/` shows the app, and `https://<host>/apix` shows the frontend's not-found page, not the backend's: the anchoring works. From a shell, an anonymous request still ends at the login:

```bash
printf 'Host: '
read -r EDGE_HOST
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' "https://${EDGE_HOST}/api/v1/healthz"
```

It prints `302` and a `cloudflareaccess.com` URL.

### 9.5 The first deploys, and launch

> **Do now.** After the pipeline's first deploy.

With `EDGE_DOMAIN` set per environment (9.3) and the routes in place, push: the pipeline deploys dev, then qa, then production ([CI/CD](./ci-cd.md#deploy-chain)), verifies each rollout and smoke tests it through a port-forward. Each environment's deployment links `DEPLOY_URL`. A deploy that fails the placeholder guard names `EDGE_DOMAIN` when the variable is missing in that environment.

**At launch** (when production goes public):
1. Delete the production host's Access application (9.1), and nothing else.
2. Check the host answers anonymously: `curl -sS -o /dev/null -w '%{http_code}\n' "https://<host>/api/v1/healthz"` prints `200`.
3. Turn on HSTS stage 1 ([section 11](#11-hsts-once-everything-is-https)).
4. Dev, qa and the previews stay behind the lab wildcard.

## 10. Static hosting for your site: prove the deploy token

> **Do now.**

Cloudflare's current recommendation for new static sites is **Workers with static assets**; Pages still works. Requests for static assets are free and unlimited. The real deploy of your site comes with its pipeline. This step only proves that the `ci-site-deploy` token from section 7 can deploy, by publishing and then deleting a one-page site on your `workers.dev` subdomain.

If you've never used Workers, open **Workers & Pages** once and choose your `workers.dev` subdomain.

```bash
source ~/cloudflare-setup.env
export CLOUDFLARE_API_TOKEN="${CF_API_TOKEN}" CLOUDFLARE_ACCOUNT_ID="${CF_ACCOUNT_ID}"
work="$(mktemp -d)" && mkdir -p "${work}/public"
printf '<!doctype html><title>deploy check</title><p>ok</p>\n' > "${work}/public/index.html"
cat > "${work}/wrangler.jsonc" <<EOF
{
  "name": "site-deploy-check",
  "compatibility_date": "$(date -u -d '-1 day' +%F 2>/dev/null || date -u -v-1d +%F)",
  "assets": { "directory": "./public" }
}
EOF
( cd "${work}" && npx --yes wrangler@4.127.1 deploy )
```

`CF_API_TOKEN` is still set from 7.2; if not, read it again as in section 3. The `compatibility_date` is set to yesterday, in UTC. Wrangler rejects a date in the future.

**Check:** wrangler prints a URL like `https://site-deploy-check.<subdomain>.workers.dev`. Then:

```bash
printf 'The workers.dev URL wrangler printed: '
read -r SITE_URL
curl -sS "${SITE_URL}" | grep -o '<p>ok</p>'
( cd "${work}" && npx --yes wrangler@4.127.1 delete --name site-deploy-check --force )
rm -rf "${work}"
unset CLOUDFLARE_API_TOKEN CF_API_TOKEN
```

The `curl` prints `<p>ok</p>`, and the site is then deleted.

The Wrangler version is pinned exactly here; pin it the same way in your site's CI (no template workflow runs Wrangler), so a new release can't change a deploy without you noticing.

If the deploy fails with `403` or `Authentication error`, the token lacks a permission. Compare it with 7.1. This is exactly the failure section 7's rule 3 is meant to catch before CI hits it.

### Put the token where CI can use it

> This is your **site repository's** setting, not the template's: no template workflow reads `CLOUDFLARE_API_TOKEN` or `CLOUDFLARE_ACCOUNT_ID` (see [Platform Setup](./platform-setup.md#used-by-a-site-repository-not-by-the-template)).

1. In your site repository on GitHub, create an environment named `production`. On a public repository this is free, with an approval gate. A private repository needs GitHub Pro or Team for environments, and Enterprise for required reviewers; see [GKE guide 11.1](./gke-cluster-setup.md#111-public-or-private).
2. Add `CLOUDFLARE_API_TOKEN` as an **environment secret** of `production`, and `CLOUDFLARE_ACCOUNT_ID` as a repository **variable** (it isn't a secret). Pass the repository to `gh` explicitly with `-R <owner>/<repo>` if you use it, never through the `GH_REPO` variable of another guide.

## 11. HSTS, once everything is HTTPS

> **Managed as code.** You can do this by hand, or leave it to your infrastructure-as-code repository. If you do it by hand, bringing it under code later needs an import (`tofu import`) or a rebuild. HSTS stays staged by hand.

HSTS tells browsers never to use plain HTTP for your domain. It's powerful, and hard to undo once browsers have cached it, so raise it in stages. A `.dev` or `.app` domain is already on the browsers' preload list: the staging below protects you from nothing there, and you can set the final value at once.

In the chart's tunnel mode the backend's own HSTS is off by default, so this setting is the only source of it ([Security, HSTS](./security.md#hsts)). Turn stage 1 on at launch (9.5), for the production zone. The lab zone may stay without it.

1. Wait until every hostname you use serves valid HTTPS. With Cloudflare in front of everything, they all will.
2. Under **SSL/TLS → Edge Certificates → HSTS**, enable it with **Max-Age 1 day**. Leave **include subdomains** and **preload** off. Run the check below.
3. After a week with no problems, raise it to **6 months** and turn on **include subdomains**.
4. Only consider **preload** once the domain will be HTTPS forever. Removing a domain from browsers' preload lists takes months.

**Check** (run it on any live hostname of the zone):

```bash
source ~/cloudflare-setup.env
curl -sS -I "https://www.${DOMAIN}" | grep -i '^strict-transport-security'
```

It prints `strict-transport-security: max-age=86400` (later `max-age=15552000; includeSubDomains`).

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
printf 'Access Key ID: '
read -rs AWS_ACCESS_KEY_ID && export AWS_ACCESS_KEY_ID
printf '\nSecret Access Key: '
read -rs AWS_SECRET_ACCESS_KEY && export AWS_SECRET_ACCESS_KEY
printf '\n'
export AWS_DEFAULT_REGION=auto
EP="https://${CF_ACCOUNT_ID}.r2.cloudflarestorage.com"
printf 'backup check %s\n' "$(date -u +%FT%TZ)" > /tmp/r2-check.txt
aws s3 cp /tmp/r2-check.txt "s3://platform-backups/pg/r2-check.txt" --endpoint-url "${EP}"
aws s3 ls "s3://platform-backups/pg/" --endpoint-url "${EP}"
aws s3 rm "s3://platform-backups/pg/r2-check.txt" --endpoint-url "${EP}"
rm /tmp/r2-check.txt
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
```

The listing shows `r2-check.txt`.

**Check that it is private.** R2's S3 endpoint refuses every unsigned request, public bucket or not, so an unsigned `curl` proves nothing. Public access goes only through the bucket's r2.dev URL or a custom domain. In the dashboard, **R2 → `platform-backups` → Settings** must show the **Public Development URL** disabled and no **Custom Domains**. Optionally, check it by API, with an account API token that has only **Workers R2 Storage: Read** and a short TTL:

```bash
source ~/cloudflare-setup.env
printf 'Paste the token, then press Enter (nothing is shown): '
read -rs CF_API_TOKEN && export CF_API_TOKEN
printf '\n'
B="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/r2/buckets/platform-backups"
curl -fsS "${B}/domains/managed" -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result.enabled'
curl -fsS "${B}/domains/custom" -H "Authorization: Bearer ${CF_API_TOKEN}" | jq -r '.result.domains | length'
unset CF_API_TOKEN
```

The first prints `false`, the second `0`.

Store the two keys as `production` environment secrets in whichever repository runs the backup job (optional, and not used by any template workflow). The pipeline work wires that up.

## 13. Final checklist and the values to hand over

> **Do now.**

| # | Check | Section | Expected |
|---|---|---|---|
| 1 | Two-factor sign-in, and you're the only member | 4 | yes |
| 2 | Cloudflare nameservers on both domains, registrant email verified | 5 | `*.ns.cloudflare.com`, no hold notice |
| 3 | Full (strict), TLS 1.2 minimum, Always Use HTTPS | 6.1 and 8.5 | 301 redirect, TLS 1.2 accepted, TLS 1.1 refused by Cloudflare |
| 4 | DNSSEC active | 6.2 | DS present, `ad` flag |
| 5 | CAA, null MX, SPF and DMARC published | 6.3 | the `dig` results |
| 6 | The API rate limit rule exists | 6.5 | listed |
| 7 | Deploy token active, with an expiry date | 7.2 | `active <date>` |
| 8 | Tunnel healthy from 2 Pods, token from Secret Manager, no LoadBalancer Services | 8.3 | Registered connections, no resources found |
| 9 | The lab wildcard blocks anonymous visitors, and the test hostname is gone | 8.4 and 8.5 | 302 to `cloudflareaccess.com`, no DNS answer |
| 10 | Notifications for tunnel health and token expiry | 8.7 | both listed |
| 11 | `EDGE_DOMAIN` set in each environment | 9.3 | the three variables |
| 12 | Routes added, anchored, catch-all last | 9.4 | `/apix` shows the frontend |
| 13 | Production behind Access until launch | 9.1 and 9.5 | 302 |
| 14 | Token can really deploy | 10 | `<p>ok</p>`, then deleted |
| 15 | HSTS at stage 1, at launch | 11 | `max-age=86400` |
| 16 | R2 is private and works, if you set it up | 12 | upload works; r2.dev off and no custom domain |

Then hand over these values. None of them is a secret:
- the domains;
- the account ID, which becomes the `CLOUDFLARE_ACCOUNT_ID` variable of your site repository;
- the tunnel name (the cluster's) and ID;
- your Zero Trust team name;
- the hostname plan from 2.2, if you changed it.

Your `~/cloudflare-setup.env` is the inventory for the infrastructure-as-code import: the account, zone, tunnel and Access application, policy and token names and IDs. Keep it, readable by you only, out of every repository.

**Never send tokens or keys.** You put those into GitHub environment secrets yourself.

## 14. Costs

> **Do now.**

These are list prices from 2026; check Cloudflare's pricing pages.

| Item | Cost |
|---|---|
| Domains | at cost, roughly $10 to $20 a year each depending on the TLD |
| DNS, edge TLS, WAF managed rules, one rate-limiting rule, DDoS protection | free plan |
| Cloudflare Tunnel | free |
| Cloudflare Access | free for up to 50 users |
| Workers static assets | static asset requests free; script requests free up to 100,000 a day |
| R2 | 10 GB-month of storage free, then about $0.015 per GB-month; egress free |

## 15. Troubleshooting

> **Do now.**

| Symptom | Likely cause | Fix |
|---|---|---|
| `dig NS` still shows the old registrar's nameservers | delegation not changed or not propagated | set Cloudflare's nameservers at the old registrar and wait up to 24 hours |
| The domain stopped resolving after a registration or transfer | the registrant's email was not verified | open the ICANN verification message (5.2); the registrar lifts the hold afterwards |
| The zone resolves badly right after the nameserver change | the old registrar still publishes a DS record | remove the DS at the old registrar, wait, then enable DNSSEC in Cloudflare and add its DS (5.1) |
| No DS record after a day | DNSSEC not enabled, or the domain is at another registrar | for a domain registered elsewhere, enable it in Cloudflare, then paste the DS record into your registrar |
| Certificate error on a new host | the Universal certificate is not Active yet | wait for it (6.1), up to a day on a new zone |
| The ExternalSecret `tunnel-token` is not Ready | the grant is missing, names another namespace, or the secret has no version | `kubectl -n cloudflared describe externalsecret tunnel-token`; check the grant and the version of `cloudflared-tunnel-token` (8.3) |
| Tunnel Pods `CreateContainerConfigError` | the Secret `tunnel-token` does not exist yet | `kubectl -n cloudflared get secret tunnel-token`; fix the ExternalSecret, which creates it |
| Pods Running but the tunnel is **Down**, with `Unauthorized` in the logs | wrong or rotated tunnel token | add a new secret version with the right token, force the sync, wait until the ExternalSecret's `.status.refreshTime` changes, then restart the connector: the operator refreshes only hourly, so an immediate restart reads the old token (the exact steps are in the rotation paragraph of 8.3) |
| Logs show connection timeouts on port 7844 | outbound traffic blocked | check Cloud NAT (GKE guide, section 6), and the egress rules of `deploy/cloudflared/networkpolicy.yaml` |
| A host returns **502 Bad Gateway** | the tunnel can't reach the Service | check that the Service URL matches `http://<svc>.<namespace>.svc.cluster.local:<port>`, that the Pod is Ready, that the target namespace carries the label `tier=app-ns` (the connector's policy blocks any other), and that no NetworkPolicy in it blocks the `cloudflared` namespace |
| `/apix` or `/x/api` reaches the backend | a route's path is unanchored | enter the path as NOTES prints it, `^/api(/\|$)`, not `/api` (9.4) |
| The app shows where the API should answer, or the API shows the app | routes are in the wrong order, or the catch-all is not last | delete and re-add the routes in NOTES' order, the catch-all last (9.4) |
| **Error 1033** | no connector is online for that tunnel | `kubectl -n cloudflared get pods`; see the rows above |
| **Error 1016**, origin DNS error | the hostname was routed before the tunnel existed, or to a deleted tunnel | delete and re-add the route |
| `dig` still answers after the route is deleted | a deleted route does not always delete its DNS record | delete the CNAME under **DNS → Records** |
| Too many redirects | the origin redirects to HTTPS while the tunnel speaks HTTP | point the route at the Service's HTTP port, and leave redirects to Cloudflare |
| A deploy fails the placeholder guard on `edge.hosts.root` | `EDGE_DOMAIN` is not set in that GitHub environment | set it (9.3) |
| Wrangler `compatibility_date` error | the date is in the future in UTC | use yesterday's UTC date, as in section 10 |
| Wrangler `403` / `Authentication error` | the token is missing a permission or the right zone | compare with 7.1; check the zone is selected under **Zone Resources** |
| The r2.dev URL or a custom domain is on (section 12's privacy check) | the bucket has public access | **R2 → bucket → Settings:** disable the Public Development URL and remove any custom domain |
| 9.2's second `curl` (the service token on the preview host) prints `302` | another Access application matches the preview host more specifically than `previews`, or `previews` lacks the Service Auth policy | under **Access controls → Applications** list every application whose hostname matches the host (the lab wildcard, `previews`, any per-host one); the `previews` application must carry the `preview-smoke` Service Auth policy and no other application may be more specific than it; delete or narrow the overlap, then repeat the check |
| A preview's smoke test lands on a login page | the service token's policy is **Allow**, not **Service Auth** | change the `preview-smoke` policy's action to **Service Auth** (9.2) |
| A preview returns 502 | the internal Gateway's address changed, the connector's egress policy to it is missing, or the preview namespace lacks the `tier: preview-app` label | rerun [GKE 12.5](./gke-cluster-setup.md#125-optional-pull-request-previews)'s last step and update the route (9.2) |

## 16. What comes next

> **Planned changes.** Everything below is not implemented yet.

These come with the pipeline work:
- **Infrastructure as code:** the zone settings, DNS records, the tunnel with its routes, Access applications and the R2 bucket, all in OpenTofu or Terraform, imported from what this guide built by hand (the IDs are in `~/cloudflare-setup.env`).
  - **Provider:** `cloudflare/cloudflare`, pinned to the v5 series. Its syntax differs from v4.
  - **State:** in a versioned, locked Google Cloud Storage bucket, never on a laptop or in Git.
  - **Changes:** reviewed as a plan in a pull request before anyone applies them.
  - **After the import,** routes and Access applications are edited in code only: a dashboard edit would be reverted by the next apply.
  - **The Secret Manager secret and its grant** of 8.3 are created there too; the token's version stays a manual step.
- **App routes:** each environment's hostname becomes one DNS record and one Access application, and the tunnel routes for a host are the NOTES table's lines, all defined in code.
- **Your site's pipeline:**
  - build, run the link and accessibility checks, then deploy with the pinned Wrangler and the environment-scoped token;
  - previews on a hostname of your own, behind Access;
  - a read-only verification step afterwards, with cold-start retries, a JSON report, and a non-zero exit code on failure.
- **Edge rules:** security headers set at the edge for the static site, and WAF exceptions where real traffic needs them.
- **Token rotation:** a scheduled workflow that warns 30 days before any token expires.
