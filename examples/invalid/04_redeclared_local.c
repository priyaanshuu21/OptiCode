/* E-SEM-002: `count` is declared twice in the same scope. */

int main() {
    int count = 1;
    int count = 2;
    printf("count=%d\n", count);
    return 0;
}
