/* E-SEM-009: the format specifier and the argument's type disagree. */

int quotient(int a, int b) {
    return a / b;
}

int main() {
    printf("3/2 = %f\n", quotient(3, 2));
    return 0;
}
