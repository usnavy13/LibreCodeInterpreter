"""Locust load testing for Code Interpreter API.

Run with:
    source .venv/bin/activate
    export API_KEY="your-api-key"
    locust -f scripts/locustfile.py --host https://localhost

Then open http://localhost:8089 in your browser to control the test.

Available User Classes:
    - CodeInterpreterUser: Full mixed workload (36 scenarios)
    - CPUUser: CPU-bound workloads only
    - MemoryUser: Memory-bound workloads only
    - IOUser: I/O-bound workloads only
    - LanguageUser: Multi-language tests only
    - LightUser: Minimal Python only (for max throughput testing)
"""

import os
from locust import HttpUser, task, between, tag

# API key from environment or default
API_KEY = os.environ.get("API_KEY", "test-api-key-for-development-only")


class CodeInterpreterUser(HttpUser):
    """Full mixed workload - all 36 scenarios."""

    wait_time = between(1, 3)

    def on_start(self):
        """Set up headers and counters."""
        self.client.verify = False
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": API_KEY,
        }
        self._iteration_counter = 0

    # =========================================================================
    # CPU-Bound Tests (6 scenarios)
    # =========================================================================

    @tag("cpu", "cpu_light")
    @task(10)
    def cpu_light(self):
        """Light CPU computation - simple math."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "result = sum(range(10000))\nprint(f'Sum: {result}')",
            },
            headers=self.headers,
            name="CPU Light",
        )

    @tag("cpu", "cpu_medium")
    @task(5)
    def cpu_medium(self):
        """Medium CPU computation - moderate math."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "result = sum(i**2 for i in range(100000))\nprint(f'Sum of squares: {result}')",
            },
            headers=self.headers,
            name="CPU Medium",
        )

    @tag("cpu", "cpu_heavy")
    @task(2)
    def cpu_heavy(self):
        """Heavy CPU computation - matrix multiplication."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import numpy as np
size = 500
a = np.random.rand(size, size)
b = np.random.rand(size, size)
c = np.dot(a, b)
print(f'Matrix: shape={c.shape}, sum={c.sum():.4f}')""",
            },
            headers=self.headers,
            name="CPU Heavy",
        )

    @tag("cpu", "cpu_sklearn")
    @task(1)
    def cpu_sklearn(self):
        """ML model training with sklearn."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.datasets import make_classification
X, y = make_classification(n_samples=500, n_features=20, n_informative=10, random_state=42)
model = RandomForestClassifier(n_estimators=50, random_state=42)
model.fit(X, y)
print(f'RandomForest score={model.score(X, y):.4f}')""",
            },
            headers=self.headers,
            name="CPU Sklearn",
        )

    @tag("cpu", "cpu_prime")
    @task(3)
    def cpu_prime(self):
        """Prime number computation."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """def is_prime(n):
    if n < 2: return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0: return False
    return True
primes = [n for n in range(10000) if is_prime(n)]
print(f'Found {len(primes)} primes, largest={primes[-1]}')""",
            },
            headers=self.headers,
            name="CPU Prime",
        )

    @tag("cpu", "cpu_fibonacci")
    @task(3)
    def cpu_fibonacci(self):
        """Fibonacci sequence computation."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
result = fib(10000)
print(f'fib={str(result)[:50]}...')""",
            },
            headers=self.headers,
            name="CPU Fibonacci",
        )

    # =========================================================================
    # Memory-Bound Tests (6 scenarios)
    # =========================================================================

    @tag("memory", "mem_10mb")
    @task(5)
    def mem_10mb(self):
        """Allocate 10MB NumPy array."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import numpy as np
size = 1310720  # 10MB
arr = np.random.rand(size)
print(f'Allocated 10MB, sum={arr.sum():.4f}')""",
            },
            headers=self.headers,
            name="Memory 10MB",
        )

    @tag("memory", "mem_50mb")
    @task(3)
    def mem_50mb(self):
        """Allocate 50MB NumPy array."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import numpy as np
size = 6553600  # 50MB
arr = np.random.rand(size)
print(f'Allocated 50MB, mean={arr.mean():.6f}')""",
            },
            headers=self.headers,
            name="Memory 50MB",
        )

    @tag("memory", "mem_100mb")
    @task(2)
    def mem_100mb(self):
        """Allocate 100MB NumPy array."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import numpy as np
size = 13107200  # 100MB
arr = np.random.rand(size)
print(f'Allocated 100MB, std={arr.std():.6f}')""",
            },
            headers=self.headers,
            name="Memory 100MB",
        )

    @tag("memory", "mem_pandas")
    @task(2)
    def mem_pandas(self):
        """1M row DataFrame operations."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import pandas as pd
import numpy as np
n_rows = 1000000
df = pd.DataFrame({
    'a': np.random.rand(n_rows),
    'b': np.random.rand(n_rows),
    'c': np.random.randint(0, 100, n_rows),
    'd': np.random.choice(['x', 'y', 'z'], n_rows),
})
grouped = df.groupby('d').agg({'a': 'mean', 'b': 'sum', 'c': 'max'})
print(f'DataFrame shape={df.shape}, memory={df.memory_usage(deep=True).sum() / 1e6:.1f}MB')""",
            },
            headers=self.headers,
            name="Memory Pandas",
        )

    @tag("memory", "mem_list")
    @task(3)
    def mem_list(self):
        """Large Python list (5M integers)."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import sys
size = 5000000
data = list(range(size))
total = sum(data)
filtered = [x for x in data if x % 2 == 0]
mem_mb = sys.getsizeof(data) / (1024 * 1024)
print(f'List size={size}, sum={total}, even_count={len(filtered)}, mem~{mem_mb:.1f}MB')""",
            },
            headers=self.headers,
            name="Memory List",
        )

    @tag("memory", "mem_dict")
    @task(3)
    def mem_dict(self):
        """Large dictionary (1M entries)."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import sys
size = 1000000
data = {i: f'value_{i}' for i in range(size)}
keys = list(data.keys())
mem_mb = sys.getsizeof(data) / (1024 * 1024)
print(f'Dict size={len(data)}, first_key={keys[0]}, mem~{mem_mb:.1f}MB')""",
            },
            headers=self.headers,
            name="Memory Dict",
        )

    # =========================================================================
    # I/O-Bound Tests (6 scenarios)
    # =========================================================================

    @tag("io", "io_small")
    @task(3)
    def io_small(self):
        """Write 10 x 100KB files."""
        self._iteration_counter += 1
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": f"""import os
for i in range(10):
    with open(f'/mnt/data/small_{{i}}.txt', 'w') as f:
        f.write('x' * 102400)
print('Created 10 x 100KB files')""",
            },
            headers=self.headers,
            name="I/O Small Files",
        )

    @tag("io", "io_large")
    @task(2)
    def io_large(self):
        """Write 3 x 1MB files."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import os
for i in range(3):
    with open(f'/mnt/data/large_{i}.txt', 'w') as f:
        f.write('y' * 1048576)
print('Created 3 x 1MB files')""",
            },
            headers=self.headers,
            name="I/O Large Files",
        )

    @tag("io", "io_matplotlib")
    @task(2)
    def io_matplotlib(self):
        """Generate matplotlib PNG plot."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
x = np.linspace(0, 10, 100)
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
ax1.plot(x, np.sin(x), 'b-')
ax2.scatter(x, np.cos(x), c=np.cos(x), cmap='viridis', s=20)
plt.savefig('/mnt/data/plot.png', dpi=100)
plt.close()
import os
print(f'Plot size: {os.path.getsize("/mnt/data/plot.png")/1024:.1f}KB')""",
            },
            headers=self.headers,
            name="I/O Matplotlib",
        )

    @tag("io", "io_csv")
    @task(3)
    def io_csv(self):
        """CSV read/write with pandas."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import pandas as pd
import numpy as np
import os
df = pd.DataFrame({
    'id': range(50000),
    'value_a': np.random.rand(50000),
    'value_b': np.random.randint(0, 1000, 50000),
})
df.to_csv('/mnt/data/data.csv', index=False)
df_read = pd.read_csv('/mnt/data/data.csv')
df_read['sum'] = df_read['value_a'] + df_read['value_b']
df_read.to_csv('/mnt/data/output.csv', index=False)
print(f'CSV size: {os.path.getsize("/mnt/data/output.csv")/1024:.0f}KB')""",
            },
            headers=self.headers,
            name="I/O CSV",
        )

    @tag("io", "io_json")
    @task(3)
    def io_json(self):
        """JSON read/write with nested data."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import json
import os
data = {
    'records': [
        {'id': i, 'name': f'Record {i}', 'values': [j * 0.1 for j in range(100)]}
        for i in range(1000)
    ]
}
with open('/mnt/data/data.json', 'w') as f:
    json.dump(data, f)
with open('/mnt/data/data.json', 'r') as f:
    loaded = json.load(f)
print(f'Records: {len(loaded["records"])}, Size: {os.path.getsize("/mnt/data/data.json")/1024:.0f}KB')""",
            },
            headers=self.headers,
            name="I/O JSON",
        )

    @tag("io", "io_image")
    @task(1)
    def io_image(self):
        """OpenCV image processing."""
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": """import cv2
import numpy as np
import os
img = np.random.randint(0, 255, (800, 1200, 3), dtype=np.uint8)
img_blur = cv2.GaussianBlur(img, (15, 15), 0)
edges = cv2.Canny(cv2.cvtColor(img_blur, cv2.COLOR_BGR2GRAY), 50, 150)
cv2.rectangle(img_blur, (100, 100), (300, 300), (0, 255, 0), 3)
cv2.imwrite('/mnt/data/processed.png', img_blur)
cv2.imwrite('/mnt/data/edges.png', edges)
print(f'Processed: {os.path.getsize("/mnt/data/processed.png")/1024:.0f}KB')""",
            },
            headers=self.headers,
            name="I/O Image",
        )

    # =========================================================================
    # Multi-Language Tests (26 scenarios - 13 languages x 2)
    # =========================================================================

    # Python
    @tag("language", "py")
    @task(2)
    def python_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "py", "code": 'print("Hello from Python")'},
            headers=self.headers,
            name="Python Baseline",
        )

    @tag("language", "py")
    @task(1)
    def python_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": 'result = sum(i*i for i in range(10000))\nprint(f"Result: {result}")',
            },
            headers=self.headers,
            name="Python Compute",
        )

    # JavaScript
    @tag("language", "js")
    @task(2)
    def javascript_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "js", "code": 'console.log("Hello from JavaScript");'},
            headers=self.headers,
            name="JavaScript Baseline",
        )

    @tag("language", "js")
    @task(1)
    def javascript_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "js",
                "code": 'let r=0; for(let i=0;i<10000;i++) r+=i*i; console.log("Result:",r);',
            },
            headers=self.headers,
            name="JavaScript Compute",
        )

    # TypeScript
    @tag("language", "ts")
    @task(2)
    def typescript_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "ts", "code": 'console.log("Hello from TypeScript");'},
            headers=self.headers,
            name="TypeScript Baseline",
        )

    @tag("language", "ts")
    @task(1)
    def typescript_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "ts",
                "code": 'let r:number=0; for(let i:number=0;i<10000;i++) r+=i*i; console.log("Result:",r);',
            },
            headers=self.headers,
            name="TypeScript Compute",
        )

    # Go
    @tag("language", "go")
    @task(2)
    def go_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "go",
                "code": 'package main\nimport "fmt"\nfunc main() { fmt.Println("Hello from Go") }',
            },
            headers=self.headers,
            name="Go Baseline",
        )

    @tag("language", "go")
    @task(1)
    def go_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "go",
                "code": 'package main\nimport "fmt"\nfunc main() { r:=0; for i:=0;i<10000;i++ { r+=i*i }; fmt.Println("Result:",r) }',
            },
            headers=self.headers,
            name="Go Compute",
        )

    # Java
    @tag("language", "java")
    @task(2)
    def java_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "java",
                "code": 'public class Main { public static void main(String[] args) { System.out.println("Hello from Java"); } }',
            },
            headers=self.headers,
            name="Java Baseline",
        )

    @tag("language", "java")
    @task(1)
    def java_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "java",
                "code": 'public class Main { public static void main(String[] args) { long r=0; for(int i=0;i<10000;i++) r+=(long)i*i; System.out.println("Result: "+r); } }',
            },
            headers=self.headers,
            name="Java Compute",
        )

    # C
    @tag("language", "c")
    @task(2)
    def c_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "c",
                "code": '#include <stdio.h>\nint main() { printf("Hello from C\\n"); return 0; }',
            },
            headers=self.headers,
            name="C Baseline",
        )

    @tag("language", "c")
    @task(1)
    def c_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "c",
                "code": '#include <stdio.h>\nint main() { long long r=0; for(int i=0;i<10000;i++) r+=(long long)i*i; printf("Result: %lld\\n",r); return 0; }',
            },
            headers=self.headers,
            name="C Compute",
        )

    # C++
    @tag("language", "cpp")
    @task(2)
    def cpp_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "cpp",
                "code": '#include <iostream>\nint main() { std::cout << "Hello from C++" << std::endl; return 0; }',
            },
            headers=self.headers,
            name="C++ Baseline",
        )

    @tag("language", "cpp")
    @task(1)
    def cpp_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "cpp",
                "code": '#include <iostream>\nint main() { long long r=0; for(int i=0;i<10000;i++) r+=(long long)i*i; std::cout << "Result: " << r << std::endl; return 0; }',
            },
            headers=self.headers,
            name="C++ Compute",
        )

    # PHP
    @tag("language", "php")
    @task(2)
    def php_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "php", "code": '<?php echo "Hello from PHP\\n"; ?>'},
            headers=self.headers,
            name="PHP Baseline",
        )

    @tag("language", "php")
    @task(1)
    def php_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "php",
                "code": '<?php $r=0; for($i=0;$i<10000;$i++) $r+=$i*$i; echo "Result: $r\\n"; ?>',
            },
            headers=self.headers,
            name="PHP Compute",
        )

    # Rust
    @tag("language", "rs")
    @task(2)
    def rust_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "rs", "code": 'fn main() { println!("Hello from Rust"); }'},
            headers=self.headers,
            name="Rust Baseline",
        )

    @tag("language", "rs")
    @task(1)
    def rust_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "rs",
                "code": 'fn main() { let r: i64 = (0..10000).map(|i: i64| i * i).sum(); println!("Result: {}", r); }',
            },
            headers=self.headers,
            name="Rust Compute",
        )

    # R
    @tag("language", "r")
    @task(2)
    def r_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "r", "code": 'print("Hello from R")'},
            headers=self.headers,
            name="R Baseline",
        )

    @tag("language", "r")
    @task(1)
    def r_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "r",
                "code": 'r <- sum((0:9999)^2)\nprint(paste("Result:", r))',
            },
            headers=self.headers,
            name="R Compute",
        )

    # Fortran
    @tag("language", "f90")
    @task(2)
    def fortran_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "f90",
                "code": 'program hello\n    print *, "Hello from Fortran"\nend program hello',
            },
            headers=self.headers,
            name="Fortran Baseline",
        )

    @tag("language", "f90")
    @task(1)
    def fortran_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "f90",
                "code": 'program compute\n    integer(8) :: r, i\n    r = 0\n    do i = 0, 9999\n        r = r + i * i\n    end do\n    print *, "Result:", r\nend program compute',
            },
            headers=self.headers,
            name="Fortran Compute",
        )

    # D
    @tag("language", "d")
    @task(2)
    def d_baseline(self):
        self.client.post(
            "/exec",
            json={
                "lang": "d",
                "code": 'import std.stdio;\nvoid main() { writeln("Hello from D"); }',
            },
            headers=self.headers,
            name="D Baseline",
        )

    @tag("language", "d")
    @task(1)
    def d_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "d",
                "code": 'import std.stdio;\nimport std.algorithm;\nimport std.range;\nvoid main() { long r = iota(0, 10000).map!(i => cast(long)i * i).sum; writeln("Result: ", r); }',
            },
            headers=self.headers,
            name="D Compute",
        )

    # Bash
    @tag("language", "bash")
    @task(2)
    def bash_baseline(self):
        self.client.post(
            "/exec",
            json={"lang": "bash", "code": 'echo "Hello from Bash"'},
            headers=self.headers,
            name="Bash Baseline",
        )

    @tag("language", "bash")
    @task(1)
    def bash_compute(self):
        self.client.post(
            "/exec",
            json={
                "lang": "bash",
                "code": 'sum=0; for i in $(seq 1 10000); do sum=$((sum + i * i)); done; echo "Result: $sum"',
            },
            headers=self.headers,
            name="Bash Compute",
        )


# =============================================================================
# Specialized User Classes for targeted testing
# =============================================================================


class CPUUser(HttpUser):
    """CPU-bound workloads only."""

    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.client.verify = False
        self.headers = {"Content-Type": "application/json", "x-api-key": API_KEY}

    @task(10)
    def cpu_light(self):
        self.client.post(
            "/exec",
            json={"lang": "py", "code": "print(sum(range(10000)))"},
            headers=self.headers,
            name="CPU Light",
        )

    @task(5)
    def cpu_medium(self):
        self.client.post(
            "/exec",
            json={"lang": "py", "code": "print(sum(i**2 for i in range(100000)))"},
            headers=self.headers,
            name="CPU Medium",
        )

    @task(2)
    def cpu_heavy(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import numpy as np; a=np.random.rand(500,500); b=np.random.rand(500,500); print(np.dot(a,b).sum())",
            },
            headers=self.headers,
            name="CPU Heavy",
        )

    @task(1)
    def cpu_sklearn(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "from sklearn.ensemble import RandomForestClassifier; from sklearn.datasets import make_classification; X,y=make_classification(500,20); m=RandomForestClassifier(50); m.fit(X,y); print(m.score(X,y))",
            },
            headers=self.headers,
            name="CPU Sklearn",
        )


class MemoryUser(HttpUser):
    """Memory-bound workloads only."""

    wait_time = between(1, 2)

    def on_start(self):
        self.client.verify = False
        self.headers = {"Content-Type": "application/json", "x-api-key": API_KEY}

    @task(5)
    def mem_10mb(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import numpy as np; arr=np.random.rand(1310720); print(arr.sum())",
            },
            headers=self.headers,
            name="Memory 10MB",
        )

    @task(3)
    def mem_50mb(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import numpy as np; arr=np.random.rand(6553600); print(arr.mean())",
            },
            headers=self.headers,
            name="Memory 50MB",
        )

    @task(2)
    def mem_100mb(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import numpy as np; arr=np.random.rand(13107200); print(arr.std())",
            },
            headers=self.headers,
            name="Memory 100MB",
        )

    @task(2)
    def mem_pandas(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import pandas as pd; import numpy as np; df=pd.DataFrame({'a':np.random.rand(1000000)}); print(df.shape)",
            },
            headers=self.headers,
            name="Memory Pandas",
        )


class IOUser(HttpUser):
    """I/O-bound workloads only."""

    wait_time = between(1, 2)

    def on_start(self):
        self.client.verify = False
        self.headers = {"Content-Type": "application/json", "x-api-key": API_KEY}

    @task(3)
    def io_files(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "for i in range(5): open(f'/mnt/data/f{i}.txt','w').write('x'*50000)\nprint('done')",
            },
            headers=self.headers,
            name="I/O Files",
        )

    @task(2)
    def io_matplotlib(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; import numpy as np; plt.plot(np.sin(np.linspace(0,10,100))); plt.savefig('/mnt/data/p.png'); print('done')",
            },
            headers=self.headers,
            name="I/O Matplotlib",
        )

    @task(3)
    def io_csv(self):
        self.client.post(
            "/exec",
            json={
                "lang": "py",
                "code": "import pandas as pd; import numpy as np; pd.DataFrame({'a':np.random.rand(10000)}).to_csv('/mnt/data/d.csv'); print('done')",
            },
            headers=self.headers,
            name="I/O CSV",
        )


class LanguageUser(HttpUser):
    """Multi-language tests only."""

    wait_time = between(0.5, 1.5)

    def on_start(self):
        self.client.verify = False
        self.headers = {"Content-Type": "application/json", "x-api-key": API_KEY}

    @task(2)
    def python(self):
        self.client.post(
            "/exec",
            json={"lang": "py", "code": 'print("Hello Python")'},
            headers=self.headers,
            name="Python",
        )

    @task(2)
    def javascript(self):
        self.client.post(
            "/exec",
            json={"lang": "js", "code": 'console.log("Hello JS");'},
            headers=self.headers,
            name="JavaScript",
        )

    @task(2)
    def go(self):
        self.client.post(
            "/exec",
            json={
                "lang": "go",
                "code": 'package main\nimport "fmt"\nfunc main(){fmt.Println("Hello Go")}',
            },
            headers=self.headers,
            name="Go",
        )

    @task(1)
    def java(self):
        self.client.post(
            "/exec",
            json={
                "lang": "java",
                "code": 'public class Main{public static void main(String[]a){System.out.println("Hello Java");}}',
            },
            headers=self.headers,
            name="Java",
        )

    @task(1)
    def rust(self):
        self.client.post(
            "/exec",
            json={"lang": "rs", "code": 'fn main(){println!("Hello Rust");}'},
            headers=self.headers,
            name="Rust",
        )

    @task(1)
    def cpp(self):
        self.client.post(
            "/exec",
            json={
                "lang": "cpp",
                "code": '#include<iostream>\nint main(){std::cout<<"Hello C++"<<std::endl;}',
            },
            headers=self.headers,
            name="C++",
        )


class LightUser(HttpUser):
    """Minimal Python only - for max throughput testing."""

    wait_time = between(0.1, 0.3)

    def on_start(self):
        self.client.verify = False
        self.headers = {"Content-Type": "application/json", "x-api-key": API_KEY}

    @task
    def minimal(self):
        self.client.post(
            "/exec",
            json={"lang": "py", "code": "print('hello')"},
            headers=self.headers,
            name="Minimal Python",
        )
