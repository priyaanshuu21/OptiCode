/* Recursion: factorial and a binary tree walk that returns early. */

int factorial(int n) {
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);
}

int fibonacci(int n) {
    if (n < 2) {
        return n;
    }
    return fibonacci(n - 1) + fibonacci(n - 2);
}

int classify(int value) {
    if (value < 0) {
        return -1;
    } else if (value == 0) {
        return 0;
    } else if (value < 10) {
        return 1;
    } else {
        return 2;
    }
}

int main() {
    printf("5! = %d\n", factorial(5));
    printf("fib(10) = %d\n", fibonacci(10));

    int probes = 5;
    printf("classify(-3) = %d\n", classify(0 - probes));
    printf("classify(0) = %d\n", classify(0));
    printf("classify(7) = %d\n", classify(7));
    printf("classify(42) = %d\n", classify(42));

    return 0;
}