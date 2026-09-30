/* Sums 1 to n with a while loop, then again with a for loop. */

int sum_while(int n) {
    int total = 0;
    int i = 1;
    while (i <= n) {
        total = total + i;
        i = i + 1;
    }
    return total;
}

int sum_for(int n) {
    int total = 0;
    for (int i = 1; i <= n; i = i + 1) {
        total = total + i;
    }
    return total;
}

int main() {
    int n = 10;
    printf("while: %d\n", sum_while(n));
    printf("for:   %d\n", sum_for(n));
    return 0;
}