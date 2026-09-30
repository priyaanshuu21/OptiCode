/* Unreachable code after a return, and a conditional return that skips a tail.
 *
 * Later passes must delete the statements after an unconditional `return` while keeping
 * the ones after a conditional one, so this shape needs to exist as a test case.
 */

int always_early(int x) {
    return x + 1;
    int dead = 99;
    return dead;
}

int sometimes_early(int x) {
    if (x > 0) {
        return 1;
    }
    /* Reached when x <= 0, so this must survive. */
    return 0;
}

int loop_with_early_return(int n) {
    int i = 0;
    while (1) {
        if (i >= n) {
            return i;
        }
        i = i + 1;
    }
    return 0;
}

int main() {
    printf("always_early(1) = %d\n", always_early(1));
    printf("sometimes_early(5) = %d\n", sometimes_early(5));
    printf("sometimes_early(0 - 5) = %d\n", sometimes_early(0 - 5));
    printf("loop_with_early_return(4) = %d\n", loop_with_early_return(4));
    return 0;
}