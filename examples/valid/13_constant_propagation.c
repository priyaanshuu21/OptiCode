/* Constant propagation: a variable assigned a literal once, then used.
 *
 * Propagating these literals is safe because nothing writes to them again. `mutable` is
 * the control case: it looks similar but is assigned in a loop, so it must not be
 * propagated.
 */

int never_reassigned() {
    int fixed = 7;
    return fixed * 2;
}

int chained() {
    int first = 3;
    int second = first + 1;
    int third = second + 1;
    return third * 10;
}

int propagated_into_call() {
    int amount = 5;
    int total = 0;
    total = total + amount;
    return total;
}

int propagated_into_condition() {
    int threshold = 10;
    if (threshold > 5) {
        return 1;
    }
    return 0;
}

int mutable(int n) {
    /* `running` changes every iteration, so it must not be treated as a constant. */
    int running = 0;
    int i = 0;
    while (i < n) {
        running = running + i;
        i = i + 1;
    }
    return running;
}

int main() {
    printf("never_reassigned() = %d\n", never_reassigned());
    printf("chained() = %d\n", chained());
    printf("propagated_into_call() = %d\n", propagated_into_call());
    printf("propagated_into_condition() = %d\n", propagated_into_condition());
    printf("mutable(5) = %d\n", mutable(5));
    return 0;
}