pub fn threads() -> usize {
    std::thread::available_parallelism().map_or(1, |n| n.get())
}
