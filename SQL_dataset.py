TASK_PROMPT = "Find all bugs in this SQL query:"

benchmark = [
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return conversion rate per campaign (orders/sessions) for non-bot users\n"
                "SELECT s.campaign_id,\n"
                "       COUNT(DISTINCT o.user_id) / COUNT(DISTINCT s.user_id) AS conversion_rate\n"
                "FROM sessions s\n"
                "LEFT JOIN orders o ON s.user_id = o.user_id AND s.campaign_id = o.campaign_id\n"
                "WHERE u.is_bot = 0\n"
                "GROUP BY s.campaign_id;",
        "solution": [
            "integer division truncates to 0 when fewer orders than sessions — should cast to float: COUNT(DISTINCT o.user_id)::float / NULLIF(COUNT(DISTINCT s.user_id), 0)",
            "WHERE references u.is_bot but table alias 'u' is never defined — should be s.is_bot or join users table",
            "no protection against division by zero — NULLIF is missing around the denominator"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return monthly revenue for each region, excluding cancelled orders,\n"
                "-- only for regions with more than 1000 total orders\n"
                "SELECT region, MONTH(order_date) AS month, SUM(amount) AS revenue\n"
                "FROM orders\n"
                "WHERE status != 'cancelled'\n"
                "GROUP BY region, MONTH(order_date)\n"
                "HAVING COUNT(*) > 1000\n"
                "ORDER BY revenue;",
        "solution": [
            "HAVING COUNT(*) > 1000 counts only non-cancelled orders per month due to WHERE filter — if the requirement is total orders per region regardless of status, the filter logic is wrong",
            "WHERE status != 'cancelled' does not exclude NULLs — rows where status IS NULL are also excluded, which may be unintended",
            "ORDER BY revenue without ASC/DESC is ambiguous in intent and orders ascending by default — likely should be DESC to show highest revenue first"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return all customers and their most recent order amount,\n"
                "-- including customers who never ordered\n"
                "SELECT c.customer_id, c.name, o.amount\n"
                "FROM customers c\n"
                "LEFT JOIN orders o ON c.customer_id = o.customer_id\n"
                "WHERE o.order_date = (SELECT MAX(order_date) FROM orders WHERE customer_id = c.customer_id)\n"
                "ORDER BY c.customer_id;",
        "solution": [
            "WHERE on right table column converts LEFT JOIN to INNER JOIN — customers with no orders are excluded because o.order_date IS NULL fails the WHERE condition",
            "correlated subquery in WHERE returns NULL for customers with no orders, which makes the condition NULL = NULL evaluate to unknown/false",
            "if a customer has multiple orders on the same max date, multiple rows are returned per customer"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should flag users who made purchases in January but NOT in February\n"
                "SELECT user_id FROM orders WHERE MONTH(order_date) = 1\n"
                "UNION\n"
                "SELECT user_id FROM orders WHERE MONTH(order_date) = 2;",
        "solution": [
            "UNION returns users who ordered in January OR February — should use EXCEPT (or MINUS in Oracle) to get January users not in February",
            "UNION deduplicates rows which hides users who ordered in both months but doesn't fix the logical error",
            "no year filter — query mixes data across all years, a user from January 2023 and February 2024 would be incorrectly excluded"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should calculate each salesperson's revenue as a percentage of their region's total\n"
                "SELECT salesperson_id, region,\n"
                "       SUM(amount) AS personal_revenue,\n"
                "       SUM(amount) / SUM(SUM(amount)) OVER () * 100 AS pct_of_total\n"
                "FROM sales\n"
                "GROUP BY salesperson_id, region;",
        "solution": [
            "OVER () computes percentage against grand total across all regions — should be OVER (PARTITION BY region) to get percentage within each region",
            "division by zero if total sales is 0 — should use NULLIF(SUM(SUM(amount)) OVER (PARTITION BY region), 0)",
            "nested aggregate SUM(SUM(amount)) is valid in window functions but only works in databases that support it — not portable across all SQL dialects"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return users who have never made a purchase and are not in the blocked list\n"
                "SELECT u.user_id, u.email\n"
                "FROM users u\n"
                "LEFT JOIN orders o ON u.user_id = o.user_id\n"
                "WHERE o.user_id IS NULL\n"
                "  AND u.user_id NOT IN (SELECT user_id FROM blocked_users);",
        "solution": [
            "NOT IN returns empty set if blocked_users contains any NULL user_id — should use NOT EXISTS instead",
            "LEFT JOIN with WHERE o.user_id IS NULL is correct for finding users with no orders, but if orders table has soft-deleted rows they may be incorrectly included",
            "no index hint or optimization — correlated NOT IN subquery can be extremely slow on large tables compared to NOT EXISTS or LEFT JOIN approach"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return the top 3 products by revenue for each category this year\n"
                "SELECT category, product_id, SUM(amount) AS revenue\n"
                "FROM sales\n"
                "WHERE YEAR(sale_date) = 2024\n"
                "GROUP BY category, product_id\n"
                "HAVING RANK() OVER (PARTITION BY category ORDER BY SUM(amount) DESC) <= 3;",
        "solution": [
            "window functions cannot be used in HAVING clause in standard SQL — RANK() OVER (...) in HAVING causes a syntax error in most databases",
            "should use a subquery or CTE: wrap the grouped query and filter on rank in an outer WHERE",
            "RANK() produces gaps when there are ties — if two products tie for rank 2, rank 3 is skipped and rank 4 appears; use DENSE_RANK() if ties should not cause gaps"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should update all orders to apply a 10% discount for premium users\n"
                "UPDATE orders\n"
                "SET amount = amount * 0.9\n"
                "WHERE user_id IN (SELECT user_id FROM users WHERE tier = 'premium')\n"
                "  AND status = 'pending';",
        "solution": [
            "running this UPDATE twice applies 19% total discount instead of 10% — no idempotency protection; should track whether discount was already applied",
            "if the subquery returns NULL user_ids, IN (... NULL ...) causes those rows to be silently skipped rather than raising an error",
            "UPDATE without a transaction means partial failure leaves data in inconsistent state — should be wrapped in BEGIN/COMMIT"
        ]
    },
]