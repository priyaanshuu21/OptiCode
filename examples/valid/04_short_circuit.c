/* Short-circuit evaluation: the right operand is only read when it must be. */

int divides(int a, int b) {
    if (b != 0 && a % b == 0) {
        return 1;
    }
    return 0;
}

int main() {
    int a = 12;
    int b = 4;
    int c = 0;

    /* Both sides are tested when the left one is true. */
    printf("12 / 4 is exact: %d\n", divides(a, b));

    /* The guard short-circuits, so the modulo is never reached with a zero divisor. */
    printf("12 / 0 is exact: %d\n", divides(a, c));

    printf("either is nonzero: %d\n", b != 0 || c != 0);
    printf("both are nonzero:  %d\n", b != 0 && c != 0);

    return 0;
}