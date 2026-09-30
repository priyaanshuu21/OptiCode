/* W-ANL-006 and W-SEM-004: the divisor is the literal 0, so the division aborts every time
 * it runs. The program is still valid MiniC, which is why it lives with the compiling
 * examples — it is the case that proves the dead-code analysis refuses to delete a
 * statement whose only effect is a guaranteed failure. */

int main() {
    int total = 10;
    printf("half=%d\n", total / 0);
    return 0;
}
