## Performance Gate

Reviewed the changed lines and found 42 performance risks.

🔴 3 Critical  🟠 19 High  🟡 18 Medium  🔵 2 Low

_Engine: Static rules only (local LLM not reachable - findings not LLM-confirmed). Everything ran on-prem; no code left the network._

### 🔴 CRITICAL — New DB connection per request (no pool)
- File: `java/src/main/java/com/example/UserService.java:10`
- Category: I/O & Database  |  Rule: `java.connection_per_request`
- Code: `return DriverManager.getConnection(`
- Why: Opening a raw JDBC connection per call skips connection pooling. TCP + auth handshakes dominate latency and the DB runs out of connections under load.
- Fix: Use a pooled DataSource (HikariCP / Spring's DataSource) and borrow connections from it.

### 🔴 CRITICAL — Database query inside a loop (N+1)
- File: `java/src/main/java/com/example/UserService.java:22`
- Category: I/O & Database  |  Rule: `java.n_plus_one`
- Code: `Statement stmt = conn.createStatement();`
- Why: A query is executed once per iteration. For N items this is N round-trips to the database - latency and DB load grow linearly with input size.
- Fix: Fetch in a single set-based query (WHERE id IN (...)), a JOIN, or a JPA batch fetch / @EntityGraph, then map results in memory.

### 🔴 CRITICAL — Database query inside a loop (N+1)
- File: `java/src/main/java/com/example/UserService.java:23`
- Category: I/O & Database  |  Rule: `java.n_plus_one`
- Code: `ResultSet rs = stmt.executeQuery("SELECT * FROM users WHERE id = " + id);`
- Why: A query is executed once per iteration. For N items this is N round-trips to the database - latency and DB load grow linearly with input size.
- Fix: Fetch in a single set-based query (WHERE id IN (...)), a JOIN, or a JPA batch fetch / @EntityGraph, then map results in memory.

### 🟠 HIGH — Timer/listener without cleanup
- File: `frontend/src/Dashboard.tsx:33`
- Category: Memory  |  Rule: `fe.missing_cleanup`
- Code: `window.addEventListener('resize', handleResize);`
- Why: A setInterval / event listener registered in an effect with no cleanup keeps firing after unmount - a classic React memory leak.
- Fix: Return a cleanup function from useEffect that clears the interval / removes the listener.

### 🟠 HIGH — Timer/listener without cleanup
- File: `frontend/src/Dashboard.tsx:34`
- Category: Memory  |  Rule: `fe.missing_cleanup`
- Code: `const interval = setInterval(fetchOrders, 5000);`
- Why: A setInterval / event listener registered in an effect with no cleanup keeps firing after unmount - a classic React memory leak.
- Fix: Return a cleanup function from useEffect that clears the interval / removes the listener.

### 🟠 HIGH — Double-checked locking without volatile
- File: `java/src/main/java/com/example/OrderProcessor.java:12`
- Category: Concurrency  |  Rule: `java.dcl_no_volatile`
- Code: `public static OrderProcessor getInstance() {`
- Why: Double-checked locking on a non-volatile field is broken under the Java Memory Model: another thread can see a partially-constructed instance.
- Fix: Mark the field volatile, or use the initialization-on-demand holder idiom / an enum singleton.

### 🟠 HIGH — Unbounded cached thread pool
- File: `java/src/main/java/com/example/OrderProcessor.java:24`
- Category: Concurrency  |  Rule: `java.unbounded_pool`
- Code: `private final ExecutorService executor = Executors.newCachedThreadPool();`
- Why: newCachedThreadPool() has no upper bound on threads. A burst of tasks spawns unlimited threads and can OOM the JVM.
- Fix: Use a bounded ThreadPoolExecutor with a fixed max pool size and a bounded queue plus a sensible rejection policy.

### 🟠 HIGH — Busy-wait / spin loop
- File: `java/src/main/java/com/example/OrderProcessor.java:28`
- Category: Concurrency  |  Rule: `java.busy_wait`
- Code: `while (!future.isDone()) {`
- Why: Spinning on a condition burns a CPU core doing no useful work while it waits.
- Fix: Block on the result instead - Future.get(), a CountDownLatch, or CompletableFuture callbacks.

### 🟠 HIGH — String concatenation in a loop
- File: `java/src/main/java/com/example/OrderProcessor.java:84`
- Category: Memory  |  Rule: `java.string_concat_loop`
- Code: `sql += "('" + record.get("id") + "', '" + record.get("name") + "', " + record.get("amount") + ")";`
- Why: Java strings are immutable, so += in a loop allocates a new String and copies the whole buffer every iteration - O(n^2) work and heavy GC pressure.
- Fix: Use a StringBuilder outside the loop and append() inside it.

### 🟠 HIGH — String concatenation in a loop
- File: `java/src/main/java/com/example/OrderProcessor.java:86`
- Category: Memory  |  Rule: `java.string_concat_loop`
- Code: `sql += ", ";`
- Why: Java strings are immutable, so += in a loop allocates a new String and copies the whole buffer every iteration - O(n^2) work and heavy GC pressure.
- Fix: Use a StringBuilder outside the loop and append() inside it.

### 🟠 HIGH — String concatenation in a loop
- File: `java/src/main/java/com/example/UserService.java:27`
- Category: Memory  |  Rule: `java.string_concat_loop`
- Code: `report += "User: " + rs.getString("name") + "\n";`
- Why: Java strings are immutable, so += in a loop allocates a new String and copies the whole buffer every iteration - O(n^2) work and heavy GC pressure.
- Fix: Use a StringBuilder outside the loop and append() inside it.

### 🟠 HIGH — String concatenation in a loop
- File: `java/src/main/java/com/example/UserService.java:28`
- Category: Memory  |  Rule: `java.string_concat_loop`
- Code: `report += "Email: " + rs.getString("email") + "\n";`
- Why: Java strings are immutable, so += in a loop allocates a new String and copies the whole buffer every iteration - O(n^2) work and heavy GC pressure.
- Fix: Use a StringBuilder outside the loop and append() inside it.

### 🟠 HIGH — String concatenation in a loop
- File: `java/src/main/java/com/example/UserService.java:29`
- Category: Memory  |  Rule: `java.string_concat_loop`
- Code: `report += "---\n";`
- Why: Java strings are immutable, so += in a loop allocates a new String and copies the whole buffer every iteration - O(n^2) work and heavy GC pressure.
- Fix: Use a StringBuilder outside the loop and append() inside it.

### 🟠 HIGH — Raw thread creation in a loop
- File: `java/src/main/java/com/example/UserService.java:61`
- Category: Concurrency  |  Rule: `java.thread_per_item`
- Code: `new Thread(() -> {`
- Why: A new OS thread is started per iteration. Thread creation is expensive and unbounded threads exhaust memory and cause context-switch thrashing under load.
- Fix: Submit tasks to a bounded, shared ExecutorService (or a virtual-thread executor) sized to your resources instead of new Thread().start().

### 🟠 HIGH — Nested loop over collections (O(n^2))
- File: `python/data_processor.py:16`
- Category: Algorithmic  |  Rule: `py.nested_loop`
- Code: `if item_a["id"] == item_b["id"]:`
- Why: Two nested loops comparing items is quadratic; it explodes as the inputs grow.
- Fix: Index one side into a dict/set keyed on the match field, then do a single linear pass - O(n) instead of O(n^2).

### 🟠 HIGH — Blocking I/O in an async function
- File: `python/data_processor.py:47`
- Category: I/O & Database  |  Rule: `py.sync_io_in_async`
- Code: `response = requests.get(f"https://api.example.com/users/{uid}")`
- Why: requests / time.sleep block the event loop, so the whole async server stalls instead of handling other requests concurrently.
- Fix: Use an async client (httpx.AsyncClient / aiohttp) and await asyncio.sleep().

### 🟠 HIGH — Blocking I/O in an async function
- File: `python/data_processor.py:49`
- Category: I/O & Database  |  Rule: `py.sync_io_in_async`
- Code: `time.sleep(0.1)  # Blocking sleep in async`
- Why: requests / time.sleep block the event loop, so the whole async server stalls instead of handling other requests concurrently.
- Fix: Use an async client (httpx.AsyncClient / aiohttp) and await asyncio.sleep().

### 🟠 HIGH — Nested loop over collections (O(n^2))
- File: `python/data_processor.py:69`
- Category: Algorithmic  |  Rule: `py.nested_loop`
- Code: `for acl in resource_acl:`
- Why: Two nested loops comparing items is quadratic; it explodes as the inputs grow.
- Fix: Index one side into a dict/set keyed on the match field, then do a single linear pass - O(n) instead of O(n^2).

### 🟠 HIGH — Opening a DB connection inside a loop
- File: `python/data_processor.py:79`
- Category: I/O & Database  |  Rule: `py.connection_per_iter`
- Code: `conn = sqlite3.connect("mydb.sqlite")`
- Why: Connecting per iteration pays the handshake cost every time and can exhaust the DB's connection limit.
- Fix: Open one connection (or use a pool) outside the loop and reuse it; batch the inserts with executemany().

### 🟠 HIGH — Function applied to an indexed column
- File: `sql/bad_queries.sql:5`
- Category: I/O & Database  |  Rule: `sql.function_on_column`
- Code: `SELECT * FROM users WHERE UPPER(email) = 'JOHN@EXAMPLE.COM';`
- Why: Wrapping a column in a function makes the predicate non-sargable, so the index on that column can't be used.
- Fix: Rewrite so the column is bare (e.g. date range instead of YEAR(col)=), or add a function-based/computed-column index.

### 🟠 HIGH — LIKE with a leading wildcard
- File: `sql/bad_queries.sql:8`
- Category: I/O & Database  |  Rule: `sql.leading_wildcard`
- Code: `SELECT * FROM products WHERE name LIKE '%widget%';`
- Why: A leading % means the index can't be used - the engine full-scans the table.
- Fix: Anchor the pattern ('abc%'), or use a full-text / trigram index for contains-search.

### 🟠 HIGH — LIKE with a leading wildcard
- File: `sql/bad_queries.sql:23`
- Category: I/O & Database  |  Rule: `sql.leading_wildcard`
- Code: `WHERE customer_id = 123 OR shipping_address LIKE '%New York%';`
- Why: A leading % means the index can't be used - the engine full-scans the table.
- Fix: Anchor the pattern ('abc%'), or use a full-text / trigram index for contains-search.

### 🟡 MEDIUM — Whole method synchronized
- File: `java/src/main/java/com/example/OrderProcessor.java:34`
- Category: Concurrency  |  Rule: `java.synchronized_method`
- Code: `public synchronized void processWithRetry(String orderId) {`
- Why: Synchronizing an entire method (especially one doing I/O) serialises all callers and holds the lock across slow operations.
- Fix: Narrow the lock to only the critical section, or use a ConcurrentHashMap / finer-grained lock.

### 🟡 MEDIUM — Object/array allocation inside a loop
- File: `java/src/main/java/com/example/OrderProcessor.java:57`
- Category: Memory  |  Rule: `java.alloc_in_loop`
- Code: `Map<String, Object> order = new HashMap<>();`
- Why: A new object/array is allocated on every iteration. If it does not depend on the loop variable it is wasted work and garbage.
- Fix: Hoist the allocation outside the loop and reuse/clear it, or size the collection once up front.

### 🟡 MEDIUM — SELECT * (over-fetching)
- File: `java/src/main/java/com/example/UserService.java:23`
- Category: Network  |  Rule: `java.select_star`
- Code: `ResultSet rs = stmt.executeQuery("SELECT * FROM users WHERE id = " + id);`
- Why: Selecting all columns fetches and serialises data you do not use, inflating network, memory and (in JPA) mapping cost.
- Fix: Select only the columns you need, or use a projection/DTO query.

### 🟡 MEDIUM — Whole method synchronized
- File: `java/src/main/java/com/example/UserService.java:39`
- Category: Concurrency  |  Rule: `java.synchronized_method`
- Code: `public synchronized Map<String, Object> getCachedData(String key) {`
- Why: Synchronizing an entire method (especially one doing I/O) serialises all callers and holds the lock across slow operations.
- Fix: Narrow the lock to only the critical section, or use a ConcurrentHashMap / finer-grained lock.

### 🟡 MEDIUM — SELECT * (over-fetching)
- File: `java/src/main/java/com/example/UserService.java:46`
- Category: Network  |  Rule: `java.select_star`
- Code: `ResultSet rs = stmt.executeQuery("SELECT * FROM cache_table WHERE cache_key = '" + key + "'");`
- Why: Selecting all columns fetches and serialises data you do not use, inflating network, memory and (in JPA) mapping cost.
- Fix: Select only the columns you need, or use a projection/DTO query.

### 🟡 MEDIUM — Autoboxing inside a loop
- File: `java/src/main/java/com/example/UserService.java:77`
- Category: MEDIUM  |  Rule: `java.autoboxing_loop`
- Code: `Integer score = rawScores[i] * 2;`
- Why: Boxing a primitive to its wrapper on every iteration allocates an object each time and adds GC pressure in hot paths.
- Fix: Work with primitive arrays / IntStream, or a primitive-specialised collection (e.g. Eclipse Collections / fastutil) instead of List<Integer>.

### 🟡 MEDIUM — Object/array allocation inside a loop
- File: `java/src/main/java/com/example/UserService.java:79`
- Category: Memory  |  Rule: `java.alloc_in_loop`
- Code: `int[] temp = new int[1000];`
- Why: A new object/array is allocated on every iteration. If it does not depend on the loop variable it is wasted work and garbage.
- Fix: Hoist the allocation outside the loop and reuse/clear it, or size the collection once up front.

### 🟡 MEDIUM — finalize() override
- File: `java/src/main/java/com/example/UserService.java:100`
- Category: Memory  |  Rule: `java.finalizer`
- Code: `protected void finalize() throws Throwable {`
- Why: Finalizers are deprecated and slow: they delay object reclamation across multiple GC cycles and stall the finalizer thread.
- Fix: Use try-with-resources / AutoCloseable or java.lang.ref.Cleaner instead.

### 🟡 MEDIUM — String concatenation in a loop
- File: `python/data_processor.py:26`
- Category: Memory  |  Rule: `py.string_concat_loop`
- Code: `line += str(value) + ","`
- Why: Building a string with += in a loop reallocates and copies repeatedly.
- Fix: Collect parts in a list and ''.join(parts) once, or use io.StringIO.

### 🟡 MEDIUM — String concatenation in a loop
- File: `python/data_processor.py:27`
- Category: Memory  |  Rule: `py.string_concat_loop`
- Code: `csv_output += line[:-1] + "\n"`
- Why: Building a string with += in a loop reallocates and copies repeatedly.
- Fix: Collect parts in a list and ''.join(parts) once, or use io.StringIO.

### 🟡 MEDIUM — Loading an entire file into memory
- File: `python/data_processor.py:33`
- Category: Memory  |  Rule: `py.read_whole_file`
- Code: `content = f.read()  # Loads entire file into memory`
- Why: f.read() pulls the whole file into RAM; large files blow up memory.
- Fix: Iterate line by line (for line in f) or read in chunks.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:2`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM orders WHERE status = 'pending';`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:5`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM users WHERE UPPER(email) = 'JOHN@EXAMPLE.COM';`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:8`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM products WHERE name LIKE '%widget%';`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:22`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM orders`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:26`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM users`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🟡 MEDIUM — Possible correlated subquery
- File: `sql/bad_queries.sql:27`
- Category: I/O & Database  |  Rule: `sql.correlated_subquery`
- Code: `WHERE id NOT IN (SELECT user_id FROM orders WHERE created_at > '2024-01-01');`
- Why: A subquery re-evaluated per outer row runs N times; on large tables it dominates runtime.
- Fix: Rewrite as a JOIN or a single aggregated/derived table where possible.

### 🟡 MEDIUM — SELECT *
- File: `sql/bad_queries.sql:54`
- Category: Network  |  Rule: `sql.select_star`
- Code: `SELECT * FROM users`
- Why: SELECT * reads and ships every column, prevents covering indexes and breaks when the schema changes.
- Fix: List only the columns you need.

### 🔵 LOW — Inline function created in render
- File: `frontend/src/Dashboard.tsx:21`
- Category: Algorithmic  |  Rule: `fe.inline_function`
- Code: `<td><button onClick={() => onSelect(order.id)}>Select</button></td>`
- Why: A new function identity every render breaks memoization of child components and can cause avoidable re-renders in large lists.
- Fix: Hoist the handler with useCallback (or define it outside the component).

### 🔵 LOW — Exceptions used for control flow
- File: `java/src/main/java/com/example/UserService.java:90`
- Category: Algorithmic  |  Rule: `java.exception_flow_control`
- Code: `Integer.parseInt(username);`
- Why: Throwing and catching exceptions to test a normal condition is far slower than a plain check - stack-trace capture is expensive.
- Fix: Validate with a cheap check (regex / Character.isDigit loop) instead of catching an exception.


Performance Gate: FAILED (found findings at or above HIGH). Recommend performance-lead review before this goes to production.
