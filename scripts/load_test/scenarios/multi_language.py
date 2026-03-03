"""Multi-language test scenarios for all 13 supported languages."""

from typing import List
from .base import BaseScenario

# Language-specific hello world and compute code
LANGUAGE_CODE = {
    "py": {
        "baseline": 'print("Hello from Python")',
        "compute": """
result = sum(i * i for i in range(10000))
print(f"Python compute result: {result}")
""",
    },
    "js": {
        "baseline": 'console.log("Hello from JavaScript");',
        "compute": """
let result = 0;
for (let i = 0; i < 10000; i++) {
    result += i * i;
}
console.log("JavaScript compute result:", result);
""",
    },
    "ts": {
        "baseline": 'console.log("Hello from TypeScript");',
        "compute": """
let result: number = 0;
for (let i: number = 0; i < 10000; i++) {
    result += i * i;
}
console.log("TypeScript compute result:", result);
""",
    },
    "go": {
        "baseline": """package main
import "fmt"
func main() {
    fmt.Println("Hello from Go")
}""",
        "compute": """package main
import "fmt"
func main() {
    result := 0
    for i := 0; i < 10000; i++ {
        result += i * i
    }
    fmt.Println("Go compute result:", result)
}""",
    },
    "java": {
        "baseline": """public class Main {
    public static void main(String[] args) {
        System.out.println("Hello from Java");
    }
}""",
        "compute": """public class Main {
    public static void main(String[] args) {
        long result = 0;
        for (int i = 0; i < 10000; i++) {
            result += (long)i * i;
        }
        System.out.println("Java compute result: " + result);
    }
}""",
    },
    "c": {
        "baseline": """#include <stdio.h>
int main() {
    printf("Hello from C\\n");
    return 0;
}""",
        "compute": """#include <stdio.h>
int main() {
    long long result = 0;
    for (int i = 0; i < 10000; i++) {
        result += (long long)i * i;
    }
    printf("C compute result: %lld\\n", result);
    return 0;
}""",
    },
    "cpp": {
        "baseline": """#include <iostream>
int main() {
    std::cout << "Hello from C++" << std::endl;
    return 0;
}""",
        "compute": """#include <iostream>
int main() {
    long long result = 0;
    for (int i = 0; i < 10000; i++) {
        result += (long long)i * i;
    }
    std::cout << "C++ compute result: " << result << std::endl;
    return 0;
}""",
    },
    "php": {
        "baseline": '<?php echo "Hello from PHP\\n"; ?>',
        "compute": """<?php
$result = 0;
for ($i = 0; $i < 10000; $i++) {
    $result += $i * $i;
}
echo "PHP compute result: $result\\n";
?>""",
    },
    "rs": {
        "baseline": """fn main() {
    println!("Hello from Rust");
}""",
        "compute": """fn main() {
    let result: i64 = (0..10000).map(|i: i64| i * i).sum();
    println!("Rust compute result: {}", result);
}""",
    },
    "r": {
        "baseline": 'print("Hello from R")',
        "compute": """
result <- sum((0:9999)^2)
print(paste("R compute result:", result))
""",
    },
    "f90": {
        "baseline": """program hello
    print *, "Hello from Fortran"
end program hello""",
        "compute": """program compute
    implicit none
    integer(8) :: result, i
    result = 0
    do i = 0, 9999
        result = result + i * i
    end do
    print *, "Fortran compute result:", result
end program compute""",
    },
    "d": {
        "baseline": """import std.stdio;
void main() {
    writeln("Hello from D");
}""",
        "compute": """import std.stdio;
import std.algorithm;
import std.range;
void main() {
    long result = iota(0, 10000).map!(i => cast(long)i * i).sum;
    writeln("D compute result: ", result);
}""",
    },
    "bash": {
        "baseline": 'echo "Hello from Bash"',
        "compute": """sum=0
for i in $(seq 0 9999); do
    sum=$((sum + i * i))
done
echo "Bash compute result: $sum"
""",
    },
}


class LanguageBaselineScenario(BaseScenario):
    """Baseline test for a specific language."""

    category = "language"
    expected_latency_range = (20, 5000)

    def __init__(self, language: str):
        self.language = language
        self.id = f"lang_{language}_baseline"
        self.name = f"Language Baseline ({language.upper()})"
        self.description = f"Hello World baseline for {language.upper()}"

    def get_code(self, iteration: int = 0) -> str:
        return LANGUAGE_CODE.get(self.language, {}).get("baseline", 'print("Hello")')


class LanguageComputeScenario(BaseScenario):
    """Compute test for a specific language."""

    category = "language"
    expected_latency_range = (50, 10000)

    def __init__(self, language: str):
        self.language = language
        self.id = f"lang_{language}_compute"
        self.name = f"Language Compute ({language.upper()})"
        self.description = f"Compute benchmark for {language.upper()}"

    def get_code(self, iteration: int = 0) -> str:
        return LANGUAGE_CODE.get(self.language, {}).get("compute", 'print("Compute")')


def get_all_language_scenarios() -> List[BaseScenario]:
    """Get baseline and compute scenarios for all languages."""
    scenarios = []
    for lang in LANGUAGE_CODE.keys():
        scenarios.append(LanguageBaselineScenario(lang))
        scenarios.append(LanguageComputeScenario(lang))
    return scenarios


def get_baseline_scenarios() -> List[BaseScenario]:
    """Get only baseline scenarios for all languages."""
    return [LanguageBaselineScenario(lang) for lang in LANGUAGE_CODE.keys()]


def get_compute_scenarios() -> List[BaseScenario]:
    """Get only compute scenarios for all languages."""
    return [LanguageComputeScenario(lang) for lang in LANGUAGE_CODE.keys()]
