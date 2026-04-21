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
            "no temporal ordering enforced — a user is counted as converted even if their order predates their session; join should include o.order_date >= s.started_at"
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
            "correlated subquery executes once per customer row making this O(n²) — for large tables use ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC) in a CTE instead",
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
            "NOT IN subquery can be extremely slow on large tables — NOT EXISTS or LEFT JOIN with IS NULL on blocked_users is significantly faster"
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
            "no tie-breaking in RANK — when two products have equal revenue, which gets rank 1 vs rank 2 is undefined; ORDER BY inside OVER() should include a secondary sort like product_id for deterministic results",
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
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return average order value per customer, only for customers with 3+ orders\n"
                "SELECT customer_id, AVG(amount) AS avg_order_value\n"
                "FROM orders\n"
                "WHERE COUNT(*) >= 3\n"
                "GROUP BY customer_id;",
        "solution": [
            "aggregate function COUNT(*) cannot be used in WHERE clause — must be moved to HAVING clause after GROUP BY",
            "AVG(amount) will silently ignore NULL amounts — if amount can be NULL, result may be misleading; consider COALESCE(amount, 0)",
            "no filter on order status — cancelled, returned, or refunded orders are included in AVG(amount), which inflates the average order value"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return each employee and their manager's name\n"
                "SELECT e.name AS employee, m.name AS manager\n"
                "FROM employees e\n"
                "JOIN employees m ON e.manager_id = m.id;",
        "solution": [
            "INNER JOIN excludes employees with no manager (e.g. CEO) — should use LEFT JOIN to include all employees",
            "query does not return employee IDs — if two employees share the same name it is impossible to distinguish them; should include e.id and m.id in SELECT",
            "no handling for circular references — if an employee is their own manager, this query could produce unexpected results or infinite loops in recursive variants"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return number of distinct active users per day for the last 30 days\n"
                "SELECT DATE(event_time) AS day, COUNT(user_id) AS active_users\n"
                "FROM events\n"
                "WHERE event_time >= NOW() - INTERVAL '30 days'\n"
                "GROUP BY DATE(event_time)\n"
                "ORDER BY day;",
        "solution": [
            "COUNT(user_id) counts all rows including duplicates — should be COUNT(DISTINCT user_id) to count unique users per day",
            "days with zero events are not returned — if continuous daily coverage is needed, must LEFT JOIN against a date series",
            "NOW() is evaluated at query start so results shift each run — for reproducible reporting use a fixed date range"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return total revenue and number of orders per product,\n"
                "-- including products that have never been ordered\n"
                "SELECT p.product_id, p.name, SUM(o.amount) AS revenue, COUNT(o.id) AS order_count\n"
                "FROM orders o\n"
                "RIGHT JOIN products p ON o.product_id = p.product_id\n"
                "GROUP BY p.product_id;",
        "solution": [
            "p.name is not included in GROUP BY — non-aggregated column in SELECT must appear in GROUP BY or this causes an error in strict SQL modes",
            "SUM(o.amount) returns NULL for products with no orders instead of 0 — should use COALESCE(SUM(o.amount), 0)",
            "COUNT(o.id) returns 0 correctly for NULLs but only if o.id is the primary key — if o.id can be NULL, COUNT may undercount"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should delete duplicate rows from the users table, keeping the most recent one\n"
                "DELETE FROM users\n"
                "WHERE id NOT IN (\n"
                "    SELECT MIN(id) FROM users GROUP BY email\n"
                ");",
        "solution": [
            "MIN(id) keeps the oldest record (lowest id), not the most recent — should use MAX(id) to keep the latest",
            "NOT IN with a subquery fails silently if any email is NULL — MIN(id) GROUP BY email will include a NULL group, and NOT IN (... NULL ...) returns no rows",
            "deleting from a table while selecting from it in a subquery is not allowed in MySQL — requires wrapping the subquery in a derived table"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return running total of sales per salesperson ordered by date\n"
                "SELECT salesperson_id, sale_date, amount,\n"
                "       SUM(amount) OVER (PARTITION BY salesperson_id ORDER BY sale_date) AS running_total\n"
                "FROM sales;",
        "solution": [
            "default window frame with ORDER BY is RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW — this groups all rows with the same sale_date together, so running total jumps unexpectedly on tied dates; use ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW for strict row-by-row accumulation",
            "outer query has no ORDER BY — without ORDER BY on the final SELECT, rows are returned in undefined order; running_total values are correct but their presentation is non-deterministic",
            "if multiple sales happen on the same date for the same salesperson, the order within that date is non-deterministic — results are not reproducible without a tiebreaker in ORDER BY"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return customers who placed orders in all of the last 3 months\n"
                "SELECT customer_id\n"
                "FROM orders\n"
                "WHERE order_date >= DATE_TRUNC('month', NOW()) - INTERVAL '3 months'\n"
                "GROUP BY customer_id\n"
                "HAVING COUNT(*) >= 3;",
        "solution": [
            "COUNT(*) >= 3 counts total orders, not distinct months — a customer with 3 orders in one month passes the filter incorrectly; should use COUNT(DISTINCT DATE_TRUNC('month', order_date)) >= 3",
            "using NOW() makes results non-deterministic — the 3-month window shifts with each execution; for auditable reports use explicit date parameters",
            "the date range includes the partial current month — a customer active in only 2 full months plus a few days of the current month could incorrectly qualify"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return the second highest salary in the company\n"
                "SELECT MAX(salary) AS second_highest\n"
                "FROM employees\n"
                "WHERE salary < (SELECT MAX(salary) FROM employees);",
        "solution": [
            "returns NULL if all employees have the same salary — no handling for the case where there is no second distinct salary",
            "does not handle ties — if multiple employees share the highest salary, this returns the highest salary among the rest, which may not be the intended second highest distinct salary",
            "returns NULL when the employees table is empty — (SELECT MAX(salary) FROM employees) returns NULL for empty table, then salary < NULL is unknown, so no rows qualify and NULL is returned with no indication of the empty table"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return users whose total spend increased month-over-month\n"
                "SELECT user_id,\n"
                "       SUM(amount) AS this_month,\n"
                "       LAG(SUM(amount)) OVER (PARTITION BY user_id ORDER BY MONTH(order_date)) AS last_month\n"
                "FROM orders\n"
                "WHERE order_date >= DATE_TRUNC('month', NOW()) - INTERVAL '2 months'\n"
                "GROUP BY user_id, MONTH(order_date)\n"
                "HAVING this_month > last_month;",
        "solution": [
            "MONTH(order_date) without YEAR causes January to follow December incorrectly — orders from different years are grouped into the same month bucket",
            "LAG returns NULL for the first month per user — HAVING this_month > last_month silently excludes users with no previous month instead of handling the NULL explicitly",
            "aliases this_month and last_month cannot be referenced in HAVING in standard SQL — must wrap in a subquery or CTE and filter in an outer WHERE"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return products where the price is above the average price in their category\n"
                "SELECT product_id, name, price, category\n"
                "FROM products\n"
                "WHERE price > AVG(price);",
        "solution": [
            "AVG(price) is an aggregate function and cannot be used in WHERE clause — must use a correlated subquery or window function",
            "AVG(price) without a category filter computes the global average, not per-category average — needs WHERE category = p.category in the subquery or AVG(price) OVER (PARTITION BY category)",
            "if a category has only one product, AVG(price) equals that product's price — price > AVG(price) is never true, so single-product categories always return no rows"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should insert a new user only if the email does not already exist\n"
                "INSERT INTO users (email, name, created_at)\n"
                "SELECT 'alice@example.com', 'Alice', NOW()\n"
                "WHERE NOT EXISTS (\n"
                "    SELECT 1 FROM users WHERE email = 'alice@example.com'\n"
                ");",
        "solution": [
            "race condition — two concurrent transactions can both pass the NOT EXISTS check before either inserts, resulting in duplicate rows",
            "SELECT without FROM is not universally supported — older MySQL versions require FROM DUAL; behavior varies across database engines making this non-portable",
            "NOW() is evaluated at statement start — if the insert is part of a long transaction, created_at may not reflect actual insert time"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return departments where average salary exceeds company average\n"
                "SELECT department_id, AVG(salary) AS dept_avg\n"
                "FROM employees\n"
                "GROUP BY department_id\n"
                "HAVING AVG(salary) > AVG(AVG(salary));",
        "solution": [
            "AVG(AVG(salary)) is a nested aggregate — this syntax is invalid in standard SQL and causes an error in most databases",
            "if a department has all NULL salaries, AVG(salary) returns NULL — HAVING NULL > (subquery) evaluates to unknown, silently excluding that department without error",
            "AVG of department averages is not the same as global average when departments have different sizes — the correct approach uses (SELECT AVG(salary) FROM employees) which computes the true company-wide average"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return the 10 most recent orders with customer names\n"
                "SELECT o.id, c.name, o.amount, o.order_date\n"
                "FROM orders o\n"
                "JOIN customers c ON o.customer_id = c.id\n"
                "ORDER BY o.order_date DESC\n"
                "LIMIT 10;",
        "solution": [
            "INNER JOIN excludes orders where customer_id is NULL or references a deleted customer — orphaned orders are silently dropped",
            "ORDER BY order_date DESC with LIMIT 10 is non-deterministic when multiple orders share the same order_date — results differ across runs without a tiebreaker like ORDER BY order_date DESC, o.id DESC",
            "no index on order_date means full table scan before LIMIT is applied — on large tables this is slow; needs an index on orders(order_date)"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should compute 7-day rolling average of daily revenue\n"
                "SELECT sale_date,\n"
                "       AVG(daily_revenue) OVER (\n"
                "           ORDER BY sale_date\n"
                "           ROWS BETWEEN 7 PRECEDING AND CURRENT ROW\n"
                "       ) AS rolling_avg\n"
                "FROM daily_sales;",
        "solution": [
            "ROWS BETWEEN 7 PRECEDING AND CURRENT ROW includes 8 rows (7 before + current), not 7 — should be ROWS BETWEEN 6 PRECEDING AND CURRENT ROW for a true 7-day window",
            "missing PARTITION BY — if daily_sales contains multiple regions or segments, the window spans across them producing incorrect cross-segment averages",
            "days with no sales are missing from daily_sales — gaps in dates cause the rolling window to silently cover more than 7 calendar days"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return users who have spent more than $1000 in total\n"
                "SELECT user_id, SUM(amount) AS total_spent\n"
                "FROM orders\n"
                "WHERE total_spent > 1000\n"
                "GROUP BY user_id;",
        "solution": [
            "column alias total_spent cannot be referenced in WHERE clause — aliases are not available at the WHERE evaluation stage; use HAVING SUM(amount) > 1000 instead",
            "no time constraint — query includes all historical orders; a user who spent $200/year over 6 years qualifies, which may not reflect current spending behavior",
            "SUM(amount) ignores NULL amounts without warning — orders with NULL amount are silently excluded from the total, potentially under-counting users who qualify"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return each product's share of total revenue as a percentage\n"
                "SELECT product_id,\n"
                "       SUM(amount) / (SELECT SUM(amount) FROM orders) * 100 AS revenue_share\n"
                "FROM orders\n"
                "GROUP BY product_id;",
        "solution": [
            "integer division if amount is an integer type — SUM(amount) / SUM(...) truncates to 0 for products with small revenue; cast to float",
            "scalar subquery (SELECT SUM(amount) FROM orders) executes once per group — should use a window function or CTE for efficiency",
            "if total revenue is 0, division by zero occurs — wrap denominator in NULLIF(..., 0)"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return all orders placed in the current calendar year\n"
                "SELECT id, customer_id, amount, order_date\n"
                "FROM orders\n"
                "WHERE YEAR(order_date) = YEAR(NOW());",
        "solution": [
            "YEAR(order_date) is not sargable — wrapping a column in a function prevents index usage, causing full table scan; use order_date >= DATE_TRUNC('year', NOW()) AND order_date < DATE_TRUNC('year', NOW()) + INTERVAL '1 year' instead",
            "YEAR() is MySQL/SQL Server specific and not standard SQL — DATE_TRUNC() is PostgreSQL specific; portability depends on target database",
            "NOW() returns current timestamp including time — on the last day of the year edge cases around midnight may behave differently across databases"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return pairs of users who share the same city\n"
                "SELECT a.user_id, b.user_id, a.city\n"
                "FROM users a\n"
                "JOIN users b ON a.city = b.city;",
        "solution": [
            "self-join without excluding same-user pairs — every user is paired with themselves (a.user_id = b.user_id); add WHERE a.user_id != b.user_id",
            "each pair is returned twice as (a,b) and (b,a) — to get unique pairs use WHERE a.user_id < b.user_id",
            "NULL city values join to nothing with INNER JOIN — users with unknown city are silently excluded; if NULL cities should be grouped together use IS NOT DISTINCT FROM instead of ="
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return transactions where the amount is an outlier (more than 3 std deviations from mean)\n"
                "SELECT id, amount\n"
                "FROM transactions\n"
                "WHERE amount > AVG(amount) + 3 * STDDEV(amount);",
        "solution": [
            "aggregate functions AVG and STDDEV cannot be used in WHERE clause — must use a subquery or CTE to compute stats first",
            "STDDEV returns NULL if there is only one row — the condition amount > NULL evaluates to unknown, silently returning no rows",
            "mean + 3*stddev only catches high outliers — low outliers (amount < mean - 3*stddev) are missed if the requirement is two-sided"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return each user's first and last purchase date\n"
                "SELECT user_id,\n"
                "       MIN(order_date) AS first_purchase,\n"
                "       MAX(order_date) AS last_purchase\n"
                "FROM orders\n"
                "GROUP BY user_id\n"
                "HAVING first_purchase != last_purchase;",
        "solution": [
            "column aliases first_purchase and last_purchase cannot be referenced in HAVING in standard SQL — must repeat the expressions: HAVING MIN(order_date) != MAX(order_date)",
            "HAVING MIN(order_date) != MAX(order_date) filters out users who made only one purchase — if the goal is to return all users with their purchase dates, the HAVING clause should be removed",
            "users with no orders are excluded — if users table exists separately, a LEFT JOIN would be needed to include users with null purchase dates"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return total number of sessions and orders per user for users active in last 7 days\n"
                "SELECT u.user_id,\n"
                "       COUNT(s.id) AS session_count,\n"
                "       COUNT(o.id) AS order_count\n"
                "FROM users u\n"
                "LEFT JOIN sessions s ON u.user_id = s.user_id\n"
                "LEFT JOIN orders o ON u.user_id = o.user_id\n"
                "WHERE s.started_at >= NOW() - INTERVAL '7 days'\n"
                "GROUP BY u.user_id;",
        "solution": [
            "WHERE on LEFT JOINed table converts it to INNER JOIN — users with no sessions in last 7 days are excluded; move condition to ON clause: LEFT JOIN sessions s ON u.user_id = s.user_id AND s.started_at >= NOW() - INTERVAL '7 days'",
            "joining both sessions and orders without aggregating first causes row multiplication — each session row is duplicated for each order row, inflating both counts; use subqueries or CTEs to aggregate separately before joining",
            "COUNT(s.id) counts NULL as 0 correctly only if s.id is a non-nullable primary key — if s.id can be NULL, count may be wrong"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should return the most popular product in each category\n"
                "SELECT category, product_id, COUNT(*) AS order_count\n"
                "FROM orders o\n"
                "JOIN products p ON o.product_id = p.product_id\n"
                "GROUP BY category, product_id\n"
                "ORDER BY category, order_count DESC;",
        "solution": [
            "ORDER BY does not limit to one row per category — the query returns all products ranked but does not filter to the top one; needs ROW_NUMBER() OVER (PARTITION BY category ORDER BY COUNT(*) DESC) in a CTE",
            "ties are not handled — if two products share the highest order count in a category, both or neither may appear depending on the approach used",
            "products with zero orders are excluded due to INNER JOIN — if the requirement includes products never ordered, use LEFT JOIN and handle NULL counts"
        ]
    },
    {
        "task": f"{TASK_PROMPT}\n\n"
                "-- Should soft-delete all inactive users (no login in 90 days)\n"
                "UPDATE users\n"
                "SET deleted_at = NOW()\n"
                "WHERE last_login < NOW() - INTERVAL '90 days'\n"
                "  AND deleted_at IS NULL;",
        "solution": [
            "users who have never logged in have last_login IS NULL — NULL < any date evaluates to unknown/false, so users who never logged in are never soft-deleted",
            "no dry-run or preview mechanism — running this directly modifies production data with no way to verify affected rows first; should SELECT before UPDATE",
            "NOW() called twice in SET and WHERE could theoretically return different timestamps in a long-running transaction — capture once in a CTE or variable"
        ]
    },
]