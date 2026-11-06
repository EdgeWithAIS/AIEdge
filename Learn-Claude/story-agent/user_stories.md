# User Stories

**Sources:**
- `meeting_notes/notes1.txt`: Requirements meeting, Checkout redesign (Priya – Product, Mark – Finance, Dev team)
- `meeting_notes/MeetingTranscript.txt`: Website Relaunch updates, Oct 15, 2026 (Sunil R, Jeff A, Ashim B, Tracy B, Jen S, Leo K)

---

## Feature 1: Checkout Redesign

### Story 1.1: Guest Checkout

**As a** customer,
**I want** to check out without creating an account,
**so that** I can finish my purchase without being forced through account registration.

**Acceptance Criteria**
- **Given** a customer without an account has items in their cart
  **When** they proceed to checkout
  **Then** they are offered a guest checkout option that does not require creating an account.
- **Given** a customer has chosen guest checkout
  **When** they complete the checkout steps
  **Then** the order is placed without an account being created.

**Open Questions**
- What information must a guest provide (for example, email for the confirmation in Story 1.2)?
- Should guests be offered the option to create an account after purchase?
- What baseline cart-abandonment metric will we use to measure success?

---

### Story 1.2: Order Confirmation Email with Tax Breakdown

**As a** Finance team member,
**I want** customers to receive an order confirmation email that includes a tax breakdown,
**so that** each order's tax amounts are clearly documented.

**Acceptance Criteria**
- **Given** a customer has successfully placed an order
  **When** the order is confirmed
  **Then** an order confirmation email is sent to the customer.
- **Given** an order confirmation email is generated
  **When** the customer views it
  **Then** it includes a breakdown of the tax applied to the order.

**Open Questions**
- What level of detail does "tax breakdown" mean (per line item, per tax jurisdiction/type, or total only)? Finance (Mark) to define.
- Does this apply to guest checkout orders as well as account holders? (Assumed yes, but not stated.)
- Is the stated benefit correct? The notes say only that "Finance needs" this, without saying why.

---

### Story 1.3: Manager Approval for Refunds Over $500

**As a** Finance team member,
**I want** refunds over $500 to require manager approval,
**so that** large refunds are reviewed before they are issued.

**Acceptance Criteria**
- **Given** a refund request for an amount over $500
  **When** the refund is submitted
  **Then** it is not processed until a manager approves it.
- **Given** a refund over $500 is pending approval
  **When** a manager approves it
  **Then** the refund is processed.
- **Given** a refund request for $500 or less
  **When** the refund is submitted
  **Then** it does not require manager approval.

**Open Questions**
- Does exactly $500 require approval? The notes say "over $500," so this story assumes $500.00 does not need approval. Please confirm.
- What happens when a manager rejects a refund? Is the requester or the customer notified?
- Who counts as a "manager" (role or permission), and how are they told an approval is pending?
- Is the $500 for a single refund or the total refunded on an order?

---

### Story 1.4: Self-Service Order Status

**As a** customer,
**I want** to see the status of my order myself,
**so that** I don't need to call Support to find out where my order is.

**Acceptance Criteria**
- **Given** a customer has placed an order
  **When** they look up that order
  **Then** they can see its current status without contacting Support.

**Open Questions**
- Where can customers view status (account page, a link in the confirmation email, an order-lookup page)?
- How do guest checkout customers (Story 1.1) look up their order without an account?
- Which order statuses should be shown (for example, processing, shipped, delivered, refunded)?

---

### Open Item (Not Yet a Story): Apple Pay Support in Phase 1

The notes list this as undecided: *"do we support Apple Pay in phase 1? Priya to confirm."* No story is written until Priya confirms the scope.

- **Owner:** Priya (Product)

---

## Feature 2: Website Relaunch

> **Status note:** Engineering (Jeff) reported the relaunch is running a little behind and the team is trying to catch up this week. This is status only, not a requirement.

### Story 2.1: Update Logo to Match Website Color Scheme

**As a** Marketing Manager,
**I want** the logo updated to match the website's color scheme,
**so that** the brand looks consistent across the relaunched website.

**Acceptance Criteria**
- **Given** the relaunched website color scheme
  **When** the updated logo is shown on the website
  **Then** its colors match the website color scheme.
- **Given** the updated logo design
  **When** it is reviewed by Marketing (Leo)
  **Then** it is approved as matching the website colors before release.

**Proposed design (from Tracy, not yet confirmed):** white background with blue that matches the website color scheme.

**Open Questions**
- Is Tracy's proposal (white background, blue matching the site colors) accepted? Who gives final sign-off: Leo, or "Tom"?
- Who is "Tom"? He is named as a stakeholder but is not on the attendee list.
- Jeff asked "Priya" to write this story, but Priya is not on this meeting's attendee list. Please confirm who owns it.
- Jen's comment about the "new icon colors" and user feedback from the form is unclear. Is that feedback positive or negative, and does it affect the logo or icon colors?

---

### Story 2.2: Show DBA Names in Website Footer

**As a** website visitor,
**I want** the footer to list all the names the company does business as (DBA),
**so that** I can recognize every business name the company operates under.

**Acceptance Criteria**
- **Given** a visitor is on any page of the website that has the footer
  **When** they view the footer
  **Then** it shows all of the company's "doing business as" (DBA) names.

**Open Questions**
- What is the full, approved list of DBA names, and who provides it (Legal, Marketing)?
- Should the DBA names replace any existing footer content, or be added to it?
- Is there a required format or wording (for example, "Company X d/b/a Y, Z")?
- The meeting doesn't state a reason for this change. Please confirm the "so that" benefit.
