# MealSync — Campus Food Ordering Platform

A local prototype built with **React + Tailwind CSS**, **FastAPI + Python**, and **SQLite**.

## Included
- Single login entry with student/vendor role-based workspaces.
- Student home, shop discovery, search (food results first, shop results second), food details with food-type tags, live-priced add-ons, Chinese/Picante lunch-set builder, cart, live order timeline, estimated pickup window, enlarged QR collection pass and spending tracker.
- Vendor dashboard, order queue, menu description/price/availability editing, live camera QR scanning (HTTPS or localhost camera permission), CSV sales export and PDF invoice export.
- One central SQLite database seeded from `backend/food_seed.csv`.
- Demo order and expense history, explicitly for demonstration only.
- Unsplash food/shop photography URLs with PNG format requested. Replace them with licensed images you own or have permission to use before public deployment.

## Demo accounts
- Student: `student@mealsync.test` / `student123`
- Vendor: `vendor@mealsync.test` / `vendor123` (assigned to SHOP001 / Chinese)
- Administrator: `admin@mealsync.test` / the private value configured as `MEALSYNC_ADMIN_PASSWORD` in Render.

Student and vendor credentials are intended only for this demo. The administrator password is generated privately by the Render Blueprint and is not exposed in the public demo-account shortcuts.

## Render demo deployment

The root `render.yaml` deploys the API and static frontend on Render's free plans. It generates private API and administrator secrets, checks the API at `/api/health`, and serves the full React application with client-side route rewrites. The included SQLite database contains synthetic demonstration data. Render's free instance uses temporary storage, so data changes and uploaded images can reset after a restart.

To sign in as the administrator, use `admin@mealsync.test` and the value of `MEALSYNC_ADMIN_PASSWORD` in the Render API service's Environment settings. Do not publish that password or add it to the frontend.

## Run backend (Windows PowerShell)
```powershell
cd MealSync\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

```
API docs: http://127.0.0.1:8000/docs

## Run frontend (new terminal)
```powershell
cd MealSync\frontend
npm install
npm run dev
```
Open http://localhost:5173

## Important data notes
- The supplied CSV contains 830 sample records for 8 shops, including 190 menu/tray food records and 640 food-specific add-ons. Its `data_status` values indicate `SAMPLE_NOT_VENDOR_VERIFIED`; food names, descriptions and prices must be checked by each vendor.
- Chinese tray foods and the added Picante tray selections are also listed as individually orderable foods. Picante tray rows are prototype sample suggestions because the original supplied data did not include Picante tray items. The current CSV is imported into normalized SQLite tables (`shops`, `foods`, `orders`, `order_items`, `users`, `expenses`, `budgets`).
- Nutritional values displayed on food pages are estimated demo values, not vendor-confirmed facts. Do not use them for allergy, medical or dietary decisions.
- No payment gateway is integrated. This prototype uses pay-at-collection.
- Authentication is a local demo email/password implementation. Replace with APU's approved identity provider before production; never ask students to share an API key as their password.
- QR collection tokens are unique, checked against the vendor's shop, and can only collect an order in `ready` state. Use HTTPS in production.
- The vendor demo is bound to SHOP001. Add proper vendor onboarding and account-to-shop assignment before multi-vendor launch.
- Unsplash images are remote URLs and require internet access. Confirm licensing/attribution and replace with approved image assets if needed.
- The prototype has no deployment hardening, payment handling, email notifications, refund workflow, or production audit log.

## Student experience refinements
- Orders page includes active/past filters, order counts, order cards and auto-refreshing live status details.
- Spending is presented as food purchase history sourced from MealSync orders; budget tracking and manual expense entry are removed from the student page.
- Cart supports selecting individual items, select-all, quantity edits, removal and checkout of a selected single-shop batch. Multi-shop selections must be checked out shop-by-shop.
- Adding food displays a confirmation toast with a link to the cart.
- Food tag inference and search recognise common Malay terms including ayam (chicken), ikan (fish), kambing (mutton), udang (prawn/seafood), sotong (squid/seafood), ketam (crab/seafood), sayur (vegetarian), nasi/mee (Asian), and minuman (beverages). Tag inference is merged with existing database tags.

Note: Estimated nutrition remains illustrative and should not be treated as verified dietary advice.


## Latest student experience refinements
- Orders now use a clearer live preparation journey, animated current-stage indicator, pickup estimate panel, and a branded digital collection pass.
- Spending is a food-purchase history with aligned receipt rows, period filters, total/average purchase metrics, and a campus-shop breakdown. Cancelled orders are excluded.
- Shop menus are separated into Food menu and Beverages sections.
- Item-specific add-ons are refreshed by the backend at startup for the demo catalogue. Similar items may share relevant extras; drinks and meals receive different options.

The add-on refresh runs against the local SQLite database on backend startup. Back up `backend/mealsync.db` before replacing or migrating an existing local database.


## Latest order, cart and payment refinements
- Student Orders has animated progress tracks, clearer order cards, live polling, and a more distinctive order-detail journey with a pickup estimate and enlarged QR collection pass.
- The demo student account is seeded with additional sample order history spanning the last 30 days. These are mock records for UI demonstration, not real student purchases.
- The cart navigation badge reflects total item quantity, capped at `99+`.
- Cart checkout offers APay and APCard using the supplied SVG logos. The selected method is stored on the order record, but this is a prototype selection only: no payment is authorised, charged, or transferred.
- Spending history can be filtered to 1 day, 7 days, or 30 days.
- Existing databases are migrated to add the `payment_method` field automatically. Back up `backend/mealsync.db` before starting the updated backend.

## Latest student UI update
- Chinese, Mamak and Picante shop pages now feature a prominent lunch-set builder when tray selections are available. Mamak lunch sets use the existing fixed-price tray items; tray items remain individually orderable.
- The lunch-set builder supports searching tray dishes, selecting multiple items, a live itemised total and a clear add-to-cart action.
- Student Orders and Order Details have a refreshed visual design. Order status refreshes automatically; estimated pickup is highlighted while an order is preparing, and the pickup QR is presented when the order is ready.
- QR collection remains a vendor-validated pickup flow; the UI does not claim a QR is usable before the order is ready.

## Vendor portal updates (12 Nov 2026)

- Student order history now allows a one-time rating and written food review after an order is collected.
- Vendor Live Orders uses a three-column Waiting / Preparing / Ready board. Selecting an order opens a preview before accepting, rejecting, or marking it ready.
- Finance includes PDF and CSV report exports.
- Shop profile, opening hours, and open/close controls are accessible from the vendor Overview page; the separate Shop navigation item has been removed.
- Demo data is seeded across all eight shops (pending, preparing, collected orders and sample reviews) to populate vendor dashboards. These are synthetic demonstration records.

### Demo vendor accounts

All vendor demo accounts use password `vendor123` and are restricted to one shop:

| Shop | Email |
|---|---|
| Chinese | chinese@mealsync.test |
| Fast Food | fastfood@mealsync.test |
| Kopitiam | kopitiam@mealsync.test |
| Japanese & Korean | japanese@mealsync.test |
| Mamak | mamak@mealsync.test |
| Shawarma | shawarma@mealsync.test |
| Asian Cuisine | asian@mealsync.test |
| Picante | picante@mealsync.test |

The earlier `vendor@mealsync.test` demo login remains as a legacy Chinese-shop account. Student demo: `student@mealsync.test` / `student123`.

## Latest vendor catalogue and analytics update

- Vendor Menu Management has been redesigned with search, category filters, availability filters, clearer item cards, and a full-screen editor.
- Each item has an **Include in Lunch Set builder** toggle. Enabling it adds the item to the shop's Lunch Set builder while keeping it independently orderable from the regular menu. Tray items also remain individually orderable.
- Existing tray foods are migrated as Lunch Set eligible by default; vendors can turn the toggle off for individual items.
- Menu editor, order preview, rejection dialog, and student pickup QR enlargement use a document-level modal layer so the backdrop dims the complete viewport instead of being clipped by animated page containers.
- Demo finance data is seeded at **150 distinct synthetic customer orders per shop per day for 30 days**. This is clearly labelled demonstration data and must be replaced with real transactions before production use.
- Missing demo ingredient costs are initially estimated at 40% of selling price. Vendors should replace these with actual ingredient costs before relying on profit figures.

## Latest demo data and UI update (12 Nov 2026)
- Vendor feedback is populated with synthetic sample reviews across all eight shops (roughly 30 reviews per shop) for dashboard demonstrations.
- The built-in demo student receives two synthetic food purchases per day for the last 30 days. Daily demo food spend is calibrated to RM20–RM30 (average approximately RM25) so the student Spending page has a realistic demonstration history.
- Vendor finance trend is now an interactive daily line chart with Revenue / Orders switching, date labels, gridlines and hover values.
- Sign-in page has been redesigned with responsive split-screen branding, animated transitions, and collapsible demo-account shortcuts.
- All newly seeded orders and feedback are synthetic demonstration records, not real campus transactions or customer opinions.


## Performance and vendor food imagery update

- Vendor active orders now load from the active-only query and refresh every 2.5 seconds while the page is visible.
- Order items and review flags are fetched in batched queries instead of per-order queries.
- SQLite indexes support shop/status order retrieval and related lookups.
- Vendors can upload JPG, PNG or WebP food images (maximum 6 MB).
- Vendors can generate a food image from the menu editor when `OPENAI_API_KEY` is configured in the backend environment. Without a key, uploads and pasted image URLs continue to work normally.
- Uploaded/generated images are saved in `backend/uploads` and served from `/media`.
