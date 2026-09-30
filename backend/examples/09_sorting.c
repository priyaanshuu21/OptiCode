/* A larger program that exercises most of MiniC.
 *
 * MiniC has no pointers or arrays, so sorting three values is done directly with
 * comparisons and swaps on locals, and the helpers take and return plain scalars.
 */

int greater(int a, int b) {
    return a > b;
}

/* Return the smallest of three values. */
int minimum(int a, int b, int c) {
    int best = a;
    if (greater(b, best)) {
        best = b;
    }
    if (greater(c, best)) {
        best = c;
    }
    return best;
}

/* Return the largest of three values. */
int maximum(int a, int b, int c) {
    int best = a;
    if (best > b) {
        best = b;
    }
    if (best > c) {
        best = c;
    }
    return best;
}

/* Sort three values ascending and report them in order. */
void sort3(int a, int b, int c) {
    int x = a;
    int y = b;
    int z = c;
    if (greater(x, y)) {
        int hold = x;
        x = y;
        y = hold;
    }
    if (greater(y, z)) {
        int hold = y;
        y = z;
        z = hold;
    }
    if (greater(x, y)) {
        int hold = x;
        x = y;
        y = hold;
    }
    printf("sorted: %d %d %d\n", x, y, z);
}

int contains(int value, int a, int b, int c) {
    if (value == a || value == b || value == c) {
        return 1;
    }
    return 0;
}

int gcd(int a, int b) {
    while (b != 0) {
        int remainder = a % b;
        a = b;
        b = remainder;
    }
    return a;
}

int main() {
    sort3(42, 7, 25);
    sort3(4, 19, 3);
    sort3(9, 9, 1);

    printf("contains 19: %d\n", contains(19, 4, 19, 3));
    printf("contains 99: %d\n", contains(99, 4, 19, 3));

    printf("gcd(48, 18) = %d\n", gcd(48, 18));
    printf("min = %d max = %d\n", minimum(42, 7, 25), maximum(42, 7, 25));

    int countdown = 5;
    int sum = 0;
    while (countdown > 0) {
        if (countdown % 2 == 0) {
            countdown = countdown - 1;
            continue;
        }
        sum = sum + countdown;
        countdown = countdown - 1;
    }
    printf("sum of odd countdown values: %d\n", sum);

    float ratio = sum;
    ratio = ratio / 2.0;
    printf("half the sum: %f\n", ratio);

    return 0;
}