/* float and int arithmetic side by side, including an explicit narrowing step. */

float scale(float value, float factor) {
    return value * factor;
}

int to_int(float value) {
    /* Narrowing a float to an int discards the fractional part. */
    int whole = value;
    return whole;
}

int main() {
    int whole = 7;
    float part = 0.5;
    float mixed = whole + part;

    printf("whole=%d part=%f mixed=%f\n", whole, part, mixed);

    float doubled = scale(mixed, 2.0);
    printf("doubled=%f\n", doubled);

    printf("as int: %d\n", to_int(doubled));

    int comparisons = 0;
    if (mixed > 7.0) {
        comparisons = comparisons + 1;
    }
    if (mixed < 7.5) {
        comparisons = comparisons + 1;
    }
    if (mixed == 7.5) {
        comparisons = comparisons + 1;
    }
    printf("true comparisons: %d of 3\n", comparisons);

    float zero = 0.0;
    int flag = !zero;
    printf("!0.0 = %d\n", flag);

    return 0;
}