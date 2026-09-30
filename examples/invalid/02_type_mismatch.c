/* E-SEM-004: an int function cannot return a string literal. */

int label() {
    return "not a number";
}

int main() {
    printf("label=%d\n", label());
    return 0;
}
