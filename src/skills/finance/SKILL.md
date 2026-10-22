---
name: finance
description: Personal finance toolchain via Plaid for querying account balances, net worth, transactions, spending analysis, recurring subscriptions/bills, credit card liabilities/due dates, and investment portfolios/trades across Capital One, SoFi, Fidelity, and other connected institutions.
metadata:
  version: 1.1.0
  category: finance
  tags: [finance, plaid, bank, sofi, capital one, fidelity, balances, transactions, spending, net worth, investments, budget, subscriptions, recurring, liabilities, credit card, due date, apr, debt, trades, dividends]
---

# Personal Finance (Plaid) Skill Guide

This skill guides your use of personal financial tools for querying real-time account balances, calculating net worth, tracking spending & transactions, auditing recurring subscriptions and bills, monitoring credit card liabilities and due dates, and analyzing investment portfolios & trade history across connected financial institutions (such as SoFi, Capital One, Fidelity).

---

## 🛠️ Finance Tool Index & Intent Mapping

| User Intent | Primary Tool | Key Parameters / Usage |
| :--- | :--- | :--- |
| **Check Balances / Net Worth** | `get_financial_summary` | No arguments. Returns aggregated cash, debt, investments, net worth, and a breakdown per institution and account. |
| **Analyze Spending / Recent Transactions** | `get_recent_transactions` | `days=30` (or `days=60`/`90`), `count=50`. Returns categorized transactions, merchants, dates, amounts, and account sources. |
| **Recurring Subscriptions & Bills** | `get_recurring_subscriptions` | No arguments. Audits all recurring outflow charges (Netflix, Spotify, gym, rent, utilities) and inflow deposits (paychecks) with frequency and estimated monthly totals. |
| **Credit Card Due Dates & Debt / Liabilities** | `get_liabilities` | No arguments. Returns credit card statement balances, minimum payments due, payment due dates, APR percentages, and overdue flags. |
| **Investment Portfolio & Asset Allocation** | `get_investment_portfolio` | No arguments. Returns holdings, tickers, quantities, current prices, and total market value (e.g. Fidelity). |
| **Investment Trade & Dividend History** | `get_investment_transactions` | `days=30`, `count=50`. Returns buy/sell transactions, dividend distributions, fees, and deposits in brokerage accounts. |
| **Refresh Financial Data on Demand** | `refresh_financial_data` | No arguments. Triggers an on-demand sync from Plaid to fetch latest bank and investment transactions. |
| **Link a New Bank Account / Reconnect** | `create_finance_link_token` | Generates a link token to launch Plaid Link in the UI. |

---

## 📖 Best Practices & Playbooks

### 1. Analyzing Spending & Transactions
When asked to analyze spending (e.g., *"analyze my spending in SoFi and Capital One"* or *"how much did I spend this month?"*):
- **Call `get_recent_transactions(days=30)` directly.**
- **Proactively generate a visual spending chart**: Call `render_chart` with a `doughnut` configuration of category totals. Do not wait for the user to ask — attach the visual breakdown directly to your answer.
- Group spending by category (Food & Drink, Groceries, Travel, Subscriptions, Utilities, Shopping, etc.) or by institution (SoFi, Capital One).
- Highlight major merchants, top expense categories, recurring charges, and notable trends.
- If the user asks for net worth or account balances alongside spending, call both `get_financial_summary` and `get_recent_transactions`.

### 2. Checking Recurring Subscriptions & Bills
When asked about subscriptions or recurring expenses (e.g., *"what subscriptions am I paying for?"* or *"list my recurring bills"*):
- **Call `get_recurring_subscriptions()` directly.**
- **Proactively generate a visual figure**: Call `render_chart` with `horizontalBar` showing monthly outflow per subscription.
- Present the breakdown clearly:
  - **Estimated Monthly Recurring Bills**: Total dollar amount per month.
  - **Subscriptions List**: Merchant name, frequency (monthly, annual, weekly), average cost, and last billing date.
  - **Income Streams**: Regular paycheck deposits and cadence (bi-weekly, monthly).

### 3. Credit Card Liabilities, Due Dates & APRs
When asked about credit cards, minimum payments, or debt (e.g., *"when is my credit card due?"* or *"what's my Capital One balance and APR?"*):
- **Call `get_liabilities()` directly.**
- Highlight:
  - **Next Payment Due Date** and **Minimum Payment Due**.
  - **Statement Balance** vs **Current Balance**.
  - **APR Breakdown** (Purchase APR, Cash Advance APR).
  - Any **Overdue Status** alerts.

### 4. Checking Balances & Net Worth
When asked about account balances (e.g., *"how much money do I have?"* or *"what is my net worth?"*):
- **Call `get_financial_summary()` directly.**
- **Proactively generate a net worth figure**: Call `render_chart` with a bar or donut chart comparing Assets (Cash, Investments) vs. Liabilities (Credit Debt).
- Provide a clean overview:
  - **Net Worth**: Total Assets minus Total Liabilities.
  - **Cash & Checking/Savings**: Available and current balances.
  - **Credit Cards & Debt**: Balances owed.
  - **Investments**: Total portfolio values.

### 5. Investment Portfolio & Trade History
When asked about stocks, ETFs, or brokerage activity (e.g., *"show my Fidelity portfolio"* or *"what trades did I make recently?"*):
- Call `get_investment_portfolio()` for current holdings, quantities, and market values.
- Call `get_investment_transactions(days=30)` for recent buy/sell activity, dividends received, and fees.

---

## 📊 Proactive Visual Figures & Charts
Whenever presenting financial breakdowns:
1. **Never just dump raw numbers or tables.** If data has categories, shares, or trends, proactively call `render_chart` (for Chart.js donut/bar/line charts) or `render_diagram` (for Mermaid Sankey cash flow diagrams).
2. For complete chart templates and code patterns, see `load_skill('financial_visualization')`.
3. Delivered figures automatically appear in Chad's Artifacts viewer and can be dispatched to Telegram.

---

## ⚠️ Guidelines & Privacy
1. All financial tools are strictly **READ-ONLY**.
2. Never ask for or store bank passwords or PINs. Account authentication is handled via secure tokenized Plaid Link.
3. Present dollar amounts formatted cleanly with commas and currency signs (e.g. `$1,250.00`).
