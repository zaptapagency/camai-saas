# CamAI — Competitive Landscape (US)

_Snapshot as of 2026-10-09. Funding/valuation figures are point-in-time and may be
stale; treat them as directional. See [Sources](#sources)._

CamAI's positioning: **turn existing CCTV into a metered, multi-vertical analytics
product** — edge-first (no raw video leaves site), camera-agnostic, one pipeline
serving many verticals (retail, parking, traffic, safety, security…), billed per
camera per month. This maps the companies closest to that.

## 1. Closest match — camera-agnostic AI video platforms

Run AI on cameras the customer already owns (our core angle).

| Company | Camera-agnostic | Edge-first (no raw video out) | Multi-vertical breadth | Pricing model | Primary focus | Funding (approx) |
|---|---|---|---|---|---|---|
| **CamAI** (us) | Yes | **Yes** — counts/events only | **Broad** (16+ verticals, 1 pipeline) | Per-camera metered SaaS | Horizontal multi-vertical | — |
| **Spot AI** | Yes (on-prem IVR, incl. analog) | Partial — records video, AI on IVR | Medium (security + video search + some ops) | Per-camera subscription + appliance | Security / VMS + AI search | ~$93M total (Oct 2024) |
| **Coram AI** | Yes (1,000+ ONVIF models) | No — cloud-recording | Medium (security + access control) | Subscription | Cloud video + access control | ~$66M (incl. $35M Series B, Jun 2026) |
| **Ambient.ai** | Yes | No — cloud VLM ("Pulsar") | Narrow — threat/security reasoning | Enterprise (sales-led) | Physical-security threat detection | not verified here |
| **Verkada** | **No** — sells own cameras | Partial — on-device AI | Medium (security suite) | Hardware + per-camera license | Full-stack hardware security | ~$5.8B valuation (Dec 2025) |
| **Rhombus** | Partial (own + some ONVIF) | Partial | Medium (security) | Hardware + license | Cloud-managed security | — |

Also in the enterprise VMS/surveillance tier (less SaaS/edge-first, more incumbent):
**Eagle Eye Networks, Genetec, Milestone, Avigilon (Motorola), BriefCam (Canon)**.

**Deep-dive teardowns:** [CamAI vs Spot AI](competitor-spot-ai.md) · **Sales battlecard:** [CamAI vs Spot AI](battlecard-spot-ai.md).

## 2. Vertical specialists (overlap specific CamAI verticals)

Single-purpose, often deeper than us in that one lane, but not multi-vertical.

| CamAI vertical | US competitors |
|---|---|
| Weapon detection | **ZeroEyes**, **Actuate** |
| PPE / forklift proximity / EHS safety | **Voxel** (~$44M Series B), **Intenseye**, **Protex AI**, **Everguard.ai** |
| Retail foot traffic / queue / crowd | **RetailNext**, **Trax**, **Standard AI**, **Density** (people-counting), **Placer.ai** (location data) |
| Parking occupancy | **Metropolis**, **Automotus** |
| Traffic / wrong-way / smart city | **Miovision**, **Rekor**, **Hayden AI**, **NoTraffic**, **Iteris** |

## 3. Horizontal CV "pipeline" platforms (dev-tooling, not packaged SaaS)

**Lumeo, Chooch, Plainsight, Camio** — build-your-own video analytics; closer to
infrastructure than a per-vertical product.

## 4. Where CamAI is differentiated

No single incumbent nails all four at once:

1. **Edge-first / no raw video leaves site.** Most (Spot, Coram, Verkada) are
   cloud-recording; our privacy story is sharper for retail/warehouse/enterprise.
2. **Multi-vertical on one pipeline.** The camera-agnostic players lean **security**;
   the vertical specialists are **single-purpose**. Retail + parking + traffic +
   safety + security on one edge agent is uncommon.
3. **Per-camera metered SaaS + self-serve onboarding.** Most enterprise players are
   sales-led annual contracts.
4. **Works with existing *and* public cameras.** Validated on live public DOT cams
   (2,300+ Caltrans feeds via the image-poll path).

## 5. Honest risks

- The camera-agnostic category (Spot AI, Coram, Ambient) is **well-funded and fast**,
  so "AI on existing cameras" is now **table stakes**, not a moat.
- **Verkada** ($5.8B) can bundle analytics into hardware and undercut on total cost
  for customers willing to swap cameras.
- Vertical specialists (ZeroEyes for weapons, Voxel for safety) may win a single
  vertical on depth + compliance/certification.
- Our moat is **breadth + edge-first privacy + pricing/onboarding**, which must be
  proven with real pilot accuracy numbers — not the pipeline alone.

## Sources

- [Best AI Video Analytics Companies — Spot AI](https://www.spot.ai/blog/best-ai-video-analytics-companies)
- [Ambient.ai competitors — Coram](https://www.coram.ai/post/ambient-ai-competitors)
- [Spot AI vs Verkada vs Coram — Coram](https://www.coram.ai/post/spot-ai-vs-verkada)
- [AI Video Analytics Companies (US vendors) — Surveillant](https://surveillant.ai/guides/ai-video-analytics-companies)
- [Spot AI funding — Clay](https://www.clay.com/dossier/spot-ai-funding)
- [Coram profile — Tracxn](https://tracxn.com/d/companies/coram/__pszTRHHjUZj0flJFjwl28--WPfBwS0CNww95A4CrtdA)
- [Verkada pre-IPO / valuation — Forge](https://forgeglobal.com/insights/how-to-invest-in-verkada-stock-pre-ipo/)
- [Voxel Coram-alternatives](https://www.voxelai.com/industry-insights/coram-alternatives)
