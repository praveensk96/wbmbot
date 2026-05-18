# Git Naming Conventions: Branch & Commit Best Practices
> Tailored for teams using **Jira** + **Bitbucket**

---

## Table of Contents

1. [Why Naming Conventions Matter](#1-why-naming-conventions-matter)
2. [Jira + Bitbucket Integration Primer](#2-jira--bitbucket-integration-primer)
3. [Branch Naming Conventions](#3-branch-naming-conventions)
   - 3.1 [Standard Format](#31-standard-format)
   - 3.2 [Branch Type Prefixes](#32-branch-type-prefixes)
   - 3.3 [Single-Ticket Branches](#33-single-ticket-branches)
   - 3.4 [Multi-Ticket Branches](#34-multi-ticket-branches)
   - 3.5 [Branches Without a Jira Ticket](#35-branches-without-a-jira-ticket)
   - 3.6 [Rules & Constraints](#36-rules--constraints)
4. [Commit Message Conventions](#4-commit-message-conventions)
   - 4.1 [Standard Format](#41-standard-format)
   - 4.2 [Commit Types](#42-commit-types)
   - 4.3 [Single-Ticket Commits](#43-single-ticket-commits)
   - 4.4 [Multi-Ticket Commits](#44-multi-ticket-commits)
   - 4.5 [Commit Body & Footer](#45-commit-body--footer)
   - 4.6 [Rules & Constraints](#46-rules--constraints)
5. [Worked Examples](#5-worked-examples)
6. [Anti-Patterns to Avoid](#6-anti-patterns-to-avoid)
7. [Quick Reference Cheat Sheet](#7-quick-reference-cheat-sheet)

---

## 1. Why Naming Conventions Matter

Consistent branch and commit naming delivers compounding benefits across the entire development lifecycle:

**Traceability** — Every branch and commit links back to a Jira ticket automatically, giving product managers, QA engineers, and stakeholders a clear audit trail without digging through code.

**Automation** — Bitbucket's smart commits and Jira's development panel rely on recognized ticket patterns to update issue statuses, log time, and add comments programmatically.

**Clarity** — A well-named branch communicates intent instantly. Reviewers understand scope before opening a single file.

**Cleaner history** — Structured commit messages make `git log`, `git bisect`, and changelog generation dramatically more useful.

**Reduced cognitive load** — When the whole team follows the same pattern, there's no guessing, no tribal knowledge required.

---

## 2. Jira + Bitbucket Integration Primer

Bitbucket detects Jira ticket references automatically — but only when the ticket key appears in a **recognized format**. The key format is your **Jira project key** (all caps) followed by a hyphen and the issue number.

```
ADVANDS-123
```

**Where ticket detection happens:**

| Location | Trigger |
|---|---|
| Branch name | Any occurrence of `ADVANDS-123` in the branch name |
| Commit message | Any occurrence of `ADVANDS-123` in the subject or body |
| Pull Request title | Any occurrence of `ADVANDS-123` |

**What Bitbucket does when it detects a ticket:**

- Displays the branch/commit/PR in the Jira issue's **Development** panel
- Allows smart commit commands (e.g., `#comment`, `#time`, `#done`) to act on the linked issue
- Enables Jira's release tracking and sprint velocity features

**Smart commit syntax (in commit messages):**

```
ADVANDS-123 #comment Fixed the null pointer exception #time 2h
ADVANDS-123 #done
```

> **Note:** Smart commits require the Jira + Bitbucket integration to be configured by your admin. The ticket key must be the first token, or appear at the start of a recognized pattern.

---

## 3. Branch Naming Conventions

### 3.1 Standard Format

```
<type>/<TICKET-ID>-<short-description>
```

All parts are **lowercase** (except the ticket ID, which must be uppercase to trigger Jira detection). Words in the description are separated by **hyphens**. No spaces, no underscores, no special characters.

```
feature/ADVANDS-456-user-authentication-flow
bugfix/ADVANDS-789-fix-null-pointer-on-login
```

---

### 3.2 Branch Type Prefixes

| Prefix | Purpose | Example |
|---|---|---|
| `feature/` | New functionality or user story | `feature/ADVANDS-101-shopping-cart` |
| `bugfix/` | Bug fix for non-production issues | `bugfix/ADVANDS-202-cart-total-calculation` |
| `hotfix/` | Urgent fix for a production issue | `hotfix/ADVANDS-303-payment-gateway-crash` |
| `chore/` | Maintenance, dependency updates, config | `chore/ADVANDS-404-upgrade-node-18` |
| `refactor/` | Code restructure with no behavior change | `refactor/ADVANDS-505-extract-auth-service` |
| `docs/` | Documentation only changes | `docs/ADVANDS-606-update-api-readme` |
| `test/` | Adding or updating tests | `test/ADVANDS-707-add-checkout-unit-tests` |
| `release/` | Release preparation branch | `release/v2.4.0` |
| `spike/` | Experimental / research work | `spike/ADVANDS-808-evaluate-graphql` |

---

### 3.3 Single-Ticket Branches

This is the most common case. One branch, one Jira ticket.

**Format:**
```
<type>/<TICKET-ID>-<short-description>
```

**Examples:**

```
feature/ADVANDS-1042-add-dark-mode-toggle
bugfix/ADVANDS-987-incorrect-date-format-on-invoice
hotfix/ADVANDS-1100-session-expiry-not-clearing-cookie
refactor/ADVANDS-876-decouple-email-service
```

**Guidelines for the description segment:**

- Use 3–6 words maximum
- Be specific enough to understand intent at a glance
- Use the imperative voice where natural (`add`, `fix`, `update`, `remove`)
- Omit filler words like `the`, `a`, `for`

---

### 3.4 Multi-Ticket Branches

Sometimes a single branch spans multiple Jira tickets — for example, when tickets are tightly coupled, part of the same epic sub-task group, or when a developer picks up related work mid-flight.

**Approach 1 — Lead ticket in the branch name, secondary tickets in commits**

Name the branch after the primary or parent ticket. Reference additional tickets in individual commit messages.

```
feature/ADVANDS-1200-checkout-redesign
```

Individual commits within this branch then reference their respective tickets:

```
feat(ADVANDS-1200): scaffold new checkout layout
feat(ADVANDS-1201): implement address autocomplete component
feat(ADVANDS-1202): add order summary sidebar
```

> ✅ This is the **recommended approach**. The branch is cleanly named, and each Jira ticket gets fine-grained development activity via commit-level linking.

---

**Approach 2 — Multi-ticket branch name (use sparingly)**

If the tickets are of equal importance and truly inseparable at the branch level, list them in the branch name separated by underscores. Keep the total branch name under 72 characters.

```
feature/ADVANDS-1200_ADVANDS-1201-checkout-and-address-redesign
```

**When to use this approach:**

- The work cannot be meaningfully split into separate branches
- Both tickets will be resolved simultaneously in the same PR
- The team explicitly agrees this is the right scope

**When NOT to use this approach:**

- You just want to avoid creating separate branches out of convenience
- One ticket clearly drives the work and the other is a side effect
- The branch name would exceed 72 characters with both IDs

---

**Approach 3 — Epic-level branch with story branches off it**

For large features spanning many tickets, create an integration branch for the epic, then create story-level branches off it.

```
feature/ADVANDS-1000-new-checkout-epic          ← epic integration branch
  └─ feature/ADVANDS-1200-checkout-layout       ← merges into epic branch
  └─ feature/ADVANDS-1201-address-autocomplete  ← merges into epic branch
  └─ feature/ADVANDS-1202-order-summary         ← merges into epic branch
```

The epic branch is eventually merged to `main`/`develop` once all stories are complete.

---

### 3.5 Branches Without a Jira Ticket

For truly untracked work (urgent typo fix, local experiment), use a `no-ticket` or `misc` prefix:

```
chore/no-ticket-fix-readme-typo
spike/no-ticket-explore-redis-caching
```

These should be rare. If the work is meaningful, create a Jira ticket first.

---

### 3.6 Rules & Constraints

| Rule | Detail |
|---|---|
| **Lowercase everything** except the ticket key | `feature/advands-123` ❌  →  `feature/ADVANDS-123` ✅ |
| **Hyphens only** as word separators | `feature/ADVANDS-123_my_branch` ❌  →  `feature/ADVANDS-123-my-branch` ✅ |
| **No spaces** | Git will reject them; shell scripts will break |
| **Max 72 characters** | Keeps branch names readable in terminals and UIs |
| **Ticket ID immediately after the prefix** | `feature/my-thing-ADVANDS-123` ❌  →  `feature/ADVANDS-123-my-thing` ✅ |
| **No trailing hyphens or slashes** | `feature/ADVANDS-123-` ❌ |
| **Don't use personal names** | `feature/johns-fix` ❌ |
| **Don't use dates in branch names** | `feature/2024-01-15-fix` ❌ — use Jira ticket instead |

---

## 4. Commit Message Conventions

### 4.1 Standard Format

Commit messages follow a structure inspired by the **Conventional Commits** specification, extended with Jira ticket references.

```
<type>(<TICKET-ID>): <short summary>

[optional body]

[optional footer]
```

**Subject line rules:**

- Maximum **72 characters**
- Use the **imperative mood**: "add feature" not "added feature" or "adding feature"
- Do **not** end with a period
- Ticket ID goes in parentheses immediately after the type

**Full example:**

```
feat(ADVANDS-1042): add dark mode toggle to user settings

Implements a persistent dark mode preference stored in localStorage
and synced to the user profile on the backend.

Resolves: ADVANDS-1042
Co-authored-by: Jane Smith <jane@example.com>
```

---

### 4.2 Commit Types

| Type | When to Use |
|---|---|
| `feat` | A new feature visible to users or consumers |
| `fix` | A bug fix |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `chore` | Build process, dependency updates, tooling |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `style` | Formatting, whitespace — no logic change |
| `perf` | Performance improvement |
| `ci` | CI/CD pipeline changes |
| `revert` | Reverts a previous commit |

---

### 4.3 Single-Ticket Commits

**Format:**
```
<type>(<TICKET-ID>): <short summary>
```

**Examples:**

```
feat(ADVANDS-1042): add dark mode toggle to settings page
fix(ADVANDS-987): correct date format on invoice to ISO 8601
refactor(ADVANDS-876): extract email sending into EmailService class
chore(ADVANDS-404): upgrade Node.js from 16 to 18
test(ADVANDS-707): add unit tests for checkout price calculation
docs(ADVANDS-606): document authentication API endpoints
```

---

### 4.4 Multi-Ticket Commits

When a single commit genuinely touches work related to more than one Jira ticket, reference all relevant tickets. Bitbucket will link the commit to each of them.

**Option A — Multiple tickets in parentheses (compact, preferred for 2 tickets):**

```
feat(ADVANDS-1200, ADVANDS-1201): add checkout layout and address component
```

**Option B — Primary ticket in subject, secondary tickets in footer:**

```
feat(ADVANDS-1200): implement checkout page redesign

Refactored layout and integrated the new address autocomplete
component built as part of the address service work.

Refs: ADVANDS-1201
Refs: ADVANDS-1202
```

**Option C — Separate commits per ticket (best practice, always prefer this):**

When commits can be logically separated, make them separate commits. This gives each Jira ticket its own granular activity log.

```
feat(ADVANDS-1200): scaffold new checkout page layout
feat(ADVANDS-1201): add address autocomplete component
feat(ADVANDS-1202): integrate order summary sidebar into checkout
```

> ✅ **Separate commits per ticket is always the cleanest approach.** Reserve multi-ticket subjects only when the changes are truly atomic and inseparable.

---

### 4.5 Commit Body & Footer

The **body** provides context that the subject line cannot. Use it to answer *why*, not *what* (the diff shows what).

```
feat(ADVANDS-1042): add dark mode toggle to user settings

The design team requested a persistent theme preference after
user research showed 62% of users preferred dark interfaces.
Preference is stored in the `user_preferences` table and applied
on page load to prevent flash of unstyled content.
```

The **footer** carries metadata:

| Footer Key | Purpose | Example |
|---|---|---|
| `Resolves:` | Marks the Jira ticket as resolved via smart commit | `Resolves: ADVANDS-1042` |
| `Refs:` | Links the commit to a ticket without closing it | `Refs: ADVANDS-1201` |
| `Closes:` | Alternative to `Resolves` | `Closes: ADVANDS-1042` |
| `BREAKING CHANGE:` | Flags a breaking API/interface change | `BREAKING CHANGE: removed legacy /v1/login endpoint` |
| `Co-authored-by:` | Credits a collaborator | `Co-authored-by: Name <email>` |

**Full commit with body and footer:**

```
fix(ADVANDS-987): correct invoice date to use ISO 8601 format

The invoice PDF was rendering dates in MM/DD/YYYY format, causing
confusion for international customers. Dates are now formatted as
YYYY-MM-DD throughout the invoice generation pipeline.

Resolves: ADVANDS-987
Refs: ADVANDS-910
```

---

### 4.6 Rules & Constraints

| Rule | Detail |
|---|---|
| **72 character subject line limit** | Git truncates beyond this in most UIs |
| **Imperative mood in subject** | "fix bug" ✅, "fixed bug" ❌, "fixes bug" ❌ |
| **No period at end of subject** | `fix(ADVANDS-1): correct typo.` ❌ |
| **Blank line between subject and body** | Required for `git log --oneline` to work correctly |
| **One logical change per commit** | Don't bundle unrelated changes — keep commits atomic |
| **Ticket ID in uppercase** | `feat(advands-123)` ❌  →  `feat(ADVANDS-123)` ✅ |
| **Don't commit directly to `main` or `develop`** | Always work in a feature branch |
| **Avoid "WIP" commits on shared branches** | Squash or fixup before merging |

---

## 5. Worked Examples

### Scenario A — Standard single-ticket feature

**Context:** You're implementing a user profile picture upload (ADVANDS-512).

```
Branch:   feature/ADVANDS-512-profile-picture-upload

Commits:
  feat(ADVANDS-512): add image upload endpoint to user API
  feat(ADVANDS-512): add S3 storage service for profile images
  feat(ADVANDS-512): add profile picture UI component
  test(ADVANDS-512): add tests for image upload validation
```

---

### Scenario B — Multi-ticket branch, one PR

**Context:** ADVANDS-600 and ADVANDS-601 are tightly related sub-tasks in the same epic — both about refactoring the payment module. They'll be reviewed and merged together.

```
Branch:   refactor/ADVANDS-600_ADVANDS-601-payment-module-cleanup

Commits:
  refactor(ADVANDS-600): extract payment gateway into PaymentService
  refactor(ADVANDS-601): remove deprecated PayPal v1 integration
  test(ADVANDS-600, ADVANDS-601): update payment service unit tests
```

---

### Scenario C — Multi-ticket branch, commits link individually

**Context:** You're working on a large checkout overhaul. ADVANDS-1200 is the parent story; ADVANDS-1201 and ADVANDS-1202 are sub-tasks discovered mid-implementation.

```
Branch:   feature/ADVANDS-1200-checkout-overhaul

Commits:
  feat(ADVANDS-1200): create new CheckoutPage component scaffold
  feat(ADVANDS-1201): implement address form with autocomplete
  feat(ADVANDS-1202): add promotional code input and validation
  fix(ADVANDS-1200): correct total calculation rounding error
  test(ADVANDS-1200): add integration tests for checkout flow

PR Title: [ADVANDS-1200, ADVANDS-1201, ADVANDS-1202] Checkout page overhaul
```

---

### Scenario D — Hotfix to production

**Context:** A payment crash in production needs an emergency fix (ADVANDS-1150).

```
Branch:   hotfix/ADVANDS-1150-payment-gateway-null-crash

Commits:
  fix(ADVANDS-1150): guard against null response from payment API

  The Stripe API occasionally returns a null `charge` object on
  network timeout. Added null check and graceful fallback to
  prevent 500 errors.

  Resolves: ADVANDS-1150
  BREAKING CHANGE: none
```

---

### Scenario E — Chore with no meaningful ticket split

**Context:** Upgrading all frontend dependencies (ADVANDS-405).

```
Branch:   chore/ADVANDS-405-dependency-upgrades-q1

Commits:
  chore(ADVANDS-405): upgrade React from 17 to 18
  chore(ADVANDS-405): upgrade Webpack from 4 to 5
  chore(ADVANDS-405): fix breaking changes from Webpack 5 migration
  test(ADVANDS-405): verify build output after dependency upgrades
```

---

## 6. Anti-Patterns to Avoid

**Vague branch names**

```
❌  feature/new-stuff
❌  bugfix/fix
❌  johns-branch
✅  feature/ADVANDS-512-user-profile-upload
```

**Missing ticket ID**

```
❌  feature/user-authentication-flow
✅  feature/ADVANDS-1042-user-authentication-flow
```

**Lowercase ticket ID (breaks Jira linking)**

```
❌  feature/advands-1042-auth-flow
✅  feature/ADVANDS-1042-auth-flow
```

**Vague or WIP commit messages**

```
❌  git commit -m "stuff"
❌  git commit -m "WIP"
❌  git commit -m "fix bug"
✅  git commit -m "fix(ADVANDS-987): correct date format on invoice"
```

**Cramming everything into one commit**

```
❌  feat(ADVANDS-100, ADVANDS-101, ADVANDS-102, ADVANDS-103): implement all checkout features
✅  Multiple focused commits, one per logical change
```

**Past tense in commit subject**

```
❌  fixed the broken login redirect
✅  fix(ADVANDS-789): correct login redirect after OAuth callback
```

**Date-based branch names**

```
❌  2024-03-15-payment-fix
✅  hotfix/ADVANDS-1150-payment-null-crash
```

**Long, rambling branch names**

```
❌  feature/ADVANDS-512-adding-the-new-profile-picture-upload-functionality-to-the-user-settings-page
✅  feature/ADVANDS-512-profile-picture-upload
```

---

## 7. Quick Reference Cheat Sheet

### Branch Name

```
<type>/<TICKET-ID>-<short-description>

feature/ADVANDS-123-add-login-page
bugfix/ADVANDS-456-fix-session-timeout
hotfix/ADVANDS-789-patch-payment-crash
```

**Multi-ticket:**
```
feature/ADVANDS-123_ADVANDS-124-checkout-redesign   ← branch name (sparingly)
feature/ADVANDS-123-checkout-redesign             ← preferred: hide extras in commits
```

### Commit Message

```
<type>(<TICKET-ID>): <short summary (max 72 chars)>

<optional body — explain WHY, not WHAT>

Resolves: ADVANDS-123
Refs: ADVANDS-124
```

**Multi-ticket:**
```
feat(ADVANDS-123, ADVANDS-124): add checkout and address components   ← compact
feat(ADVANDS-123): add checkout page                               ← preferred: separate commits
```

### Type Quick Reference

| Prefix (branch) | Type (commit) | Use for |
|---|---|---|
| `feature/` | `feat` | New functionality |
| `bugfix/` | `fix` | Bug fixes |
| `hotfix/` | `fix` | Production urgent fix |
| `chore/` | `chore` | Maintenance / tooling |
| `refactor/` | `refactor` | Code restructuring |
| `docs/` | `docs` | Documentation |
| `test/` | `test` | Tests only |
| `spike/` | `chore` | Research / experiments |

### Golden Rules

1. **Ticket ID always uppercase** — `ADVANDS-123`, never `advands-123`
2. **Ticket ID in branch** = Jira sees your branch; **Ticket ID in commit** = Jira sees your commits
3. **One logical change per commit** — makes revert, bisect, and review painless
4. **72-character subject line** — always
5. **When in doubt, separate commits** — it's always easier to squash than to split
