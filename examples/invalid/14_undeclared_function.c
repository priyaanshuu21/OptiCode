/* E-SEM-001: `helper` is called but never declared. MiniC has no implicit declarations,
 * so a typo in a function name is a hard error rather than a link-time surprise. */

int main() {
    printf("answer=%d\n", hepler(3));
    return 0;
}
