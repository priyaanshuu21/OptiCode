/* E-SEM-008: `g` is a void function, so its call has no value to initialise `x`. */

void g() {
    printf("g\n");
}

int main() {
    int x = g();
    printf("x=%d\n", x);
    return 0;
}
