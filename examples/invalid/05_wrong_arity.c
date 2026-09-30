/* E-SEM-006: `add` is called with two arguments but declared with one. */

int add(int a) {
    return a + 1;
}

int main() {
    printf("add(1, 2) = %d\n", add(1, 2));
    return 0;
}
