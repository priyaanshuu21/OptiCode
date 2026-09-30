/* Dead assignments: writes to variables that are never read afterwards.
 *
 * Each variable below is assigned and then either overwritten or never used, so a dead
 * store pass has something real to remove without changing the result.
 */

int overwritten_before_read() {
    int a = 1;
    a = 2;
    a = 3;
    return a;
}

int dead_in_branch(int flag) {
    int used = 0;
    if (flag) {
        int only_here = 10;
        used = 1;
    } else {
        used = 2;
    }
    return used;
}

int dead_loop_value(int n) {
    int total = 0;
    for (int i = 0; i < n; i = i + 1) {
        int scratch = i * 2;
        total = total + i;
    }
    return total;
}

int self_assignment(int x) {
    int same = x;
    same = same;
    return same;
}

int main() {
    printf("overwritten_before_read() = %d\n", overwritten_before_read());
    printf("dead_in_branch(1) = %d\n", dead_in_branch(1));
    printf("dead_in_branch(0) = %d\n", dead_in_branch(0));
    printf("dead_loop_value(5) = %d\n", dead_loop_value(5));
    printf("self_assignment(7) = %d\n", self_assignment(7));
    return 0;
}