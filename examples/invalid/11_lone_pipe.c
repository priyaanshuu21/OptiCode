/* E-LEX-007: a single `|` is a typo for `||`, and guessing would be worse than saying so. */

int main() {
    int a = 1;
    int b = 2;
    if (a | b) {
        return 1;
    }
    return 0;
}
