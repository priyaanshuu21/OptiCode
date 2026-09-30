/* W-ANL-004: `total` is declared with no value, and the read below has no definition
 * reaching it, so it observes whatever the memory happened to hold. */

int main() {
    int total;
    printf("total=%d\n", total);
    return 0;
}
